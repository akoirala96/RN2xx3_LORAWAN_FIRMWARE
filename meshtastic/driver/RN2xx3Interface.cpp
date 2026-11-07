// SPDX-License-Identifier: GPL-3.0-or-later
#if defined(ARCH_PORTDUINO)
#include "RN2xx3Interface.h"
#include "configuration.h"
#include "Router.h"
#include "MeshService.h"
#include "airtime.h"
#include "main.h"
#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <vector>

RN2xx3Interface::RN2xx3Interface(const std::string &device)
    : concurrency::OSThread("RN2xx3",20), path(device) {
    const char *gate = std::getenv("MESHTASTIC_RN2XX3_TX");
    transmitAllowed = gate && std::string(gate) == "1";
}

RN2xx3Interface::~RN2xx3Interface() {
    std::lock_guard<std::recursive_mutex> guard(mutex);
    ready = false;
    if (uart && paused) {
        try {
            uart->command("radio rxstop");
            for (auto &[name,value] : original) uart->requireOk("radio set " + name + " " + value);
            uart->requireOk("mac resume");
        } catch (...) { /* USB removal: no flash settings were changed. */ }
    }
    while (!queue.empty()) packetPool.release(queue.dequeue());
    if (sendingPacket) packetPool.release(sendingPacket);
}

bool RN2xx3Interface::init() {
    std::lock_guard<std::recursive_mutex> guard(mutex);
    try {
        uart = std::make_unique<rn2xx3::Serial>(path);
        while (!uart->readLine(0).empty()) {}
        const auto version = uart->command("sys get ver");
        if (version.rfind("RN2903 ",0) != 0) throw std::runtime_error("RN2903 required");
        const std::vector<std::string> fields{"mod","freq","pwr","sf","bw","cr","crc","iqi","prlen","sync","wdt"};
        for (const auto &field : fields) {
            auto value = uart->command("radio get " + field);
            if (value.empty() || value == "invalid_param") throw std::runtime_error("Missing radio control");
            original[field] = value;
        }
        auto value = uart->command("mac pause");
        if (value.empty() || value.find_first_not_of("0123456789") != std::string::npos)
            throw std::runtime_error("Raw radio unavailable");
        paused = true;
        // RX persists in the modem after a host process exits. Stop it before
        // applying a profile so supervisor restarts do not fail with busy.
        uart->requireOk("radio rxstop");
        RadioInterface::init();
        ready = true;
        if (!applyProfile()) return false;
        LOG_INFO("RN2903 host radio initialized; TX gate=%s",transmitAllowed ? "enabled" : "disabled");
        return true;
    } catch (const std::exception &error) {
        LOG_ERROR("RN2903 initialization failed: %s",error.what());
        ready = false;
        return false;
    }
}

bool RN2xx3Interface::applyProfile() {
    configured = false;
    if (config.lora.region == meshtastic_Config_LoRaConfig_RegionCode_UNSET) {
        LOG_WARN("RN2903 inactive until the US region is configured");
        return true; // Keep the local client API usable for initial provisioning.
    }
    if (config.lora.region != meshtastic_Config_LoRaConfig_RegionCode_US ||
        savedFreq < 902.0f || savedFreq > 928.0f ||
        (bw != 125 && bw != 250 && bw != 500) || sf < 7 || sf > 12 || cr < 5 || cr > 8)
    {
        LOG_ERROR("RN2903 profile unsupported; radio inactive, client API remains available");
        return true;
    }
    limitPower(17);
    power=std::clamp<int>(power,2,17);
    config.lora.tx_power=power;
    const auto hz = static_cast<uint32_t>(std::lround(savedFreq * 1000000.0));
    const std::vector<std::pair<std::string,std::string>> profile{
        {"mod","lora"},{"freq",std::to_string(hz)},{"sf","sf"+std::to_string(sf)},
        {"bw",std::to_string(static_cast<int>(bw))},{"cr","4/"+std::to_string(cr)},
        {"crc","on"},{"iqi","off"},{"prlen",std::to_string(preambleLength)},
        {"sync","2b"},{"wdt","0"},{"pwr",std::to_string(std::clamp<int>(power,2,17))}};
    for (const auto &[name,value] : profile) {
        if (uart->command("radio set " + name + " " + value) != "ok")
            throw std::runtime_error("RN2903 rejected radio setting: "+name+"="+value);
        auto actual = uart->command("radio get " + name);
        std::transform(actual.begin(),actual.end(),actual.begin(),[](unsigned char c){return std::tolower(c);});
        if (actual != value) throw std::runtime_error("RN2903 profile readback mismatch: "+name);
    }
    configured = true;
    startReceive();
    LOG_INFO("RN2903 US profile verified: freq=%u SF=%u BW=%u CR=4/%u",hz,sf,static_cast<unsigned>(bw),cr);
    return true;
}

bool RN2xx3Interface::reconfigure() {
    std::lock_guard<std::recursive_mutex> guard(mutex);
    if (!ready) return false;
    while (!queue.empty()) packetPool.release(queue.dequeue());
    due = 0;
    if (sendingPacket) { pendingProfile = true; configured = false; return true; }
    try { stopReceive(); RadioInterface::reconfigure(); return applyProfile(); }
    catch (...) { configured = false; LOG_ERROR("RN2903 profile rejected; radio left inactive"); return false; }
}

void RN2xx3Interface::startReceive() {
    if (configured && !disabled && !sendingPacket && !receiving) {
        uart->requireOk("radio rx 0"); receiving = true;
    }
}
void RN2xx3Interface::stopReceive() {
    if (receiving) { uart->requireOk("radio rxstop"); receiving = false; }
}
bool RN2xx3Interface::sleep() {
    std::lock_guard<std::recursive_mutex> guard(mutex);
    LOG_INFO("RN2903 sleep requested");
    try { stopReceive(); return true; } catch (...) { return false; }
}

ErrorCode RN2xx3Interface::send(meshtastic_MeshPacket *packet) {
    std::lock_guard<std::recursive_mutex> guard(mutex);
    if (!ready || !configured || disabled || !transmitAllowed || !config.lora.tx_enabled ||
        config.lora.region != meshtastic_Config_LoRaConfig_RegionCode_US ||
        packet->which_payload_variant != meshtastic_MeshPacket_encrypted_tag ||
        packet->encrypted.size + MESHTASTIC_HEADER_LENGTH > MAX_LORA_PAYLOAD_LEN) {
        LOG_INFO("RN2903 send rejected: ready=%d configured=%d disabled=%d gate=%d config_tx=%d variant=%d bytes=%u",ready,configured,disabled,transmitAllowed,config.lora.tx_enabled,packet->which_payload_variant,packet->encrypted.size);
        packetPool.release(packet); return ERRNO_DISABLED;
    }
    bool dropped = false;
    if (!queue.enqueue(packet,&dropped)) { packetPool.release(packet); return ERRNO_UNKNOWN; }
    LOG_INFO("RN2903 encrypted packet queued");
    schedule();
    return ERRNO_OK;
}
void RN2xx3Interface::schedule() {
    if (!queue.empty() && !due) {
        auto *packet = queue.getFront();
        due = millis() + ((packet->rx_snr || packet->rx_rssi) ? getTxDelayMsecWeighted(packet) : getTxDelayMsec());
    }
}
meshtastic_QueueStatus RN2xx3Interface::getQueueStatus() {
    meshtastic_QueueStatus status{}; status.free = queue.getFree(); status.maxlen = queue.getMaxLen(); return status;
}
bool RN2xx3Interface::cancelSending(NodeNum from,PacketId id) {
    auto *packet = queue.remove(from,id); if (packet) packetPool.release(packet); return packet != nullptr;
}
bool RN2xx3Interface::findInTxQueue(NodeNum from,PacketId id) { return queue.find(from,id); }

bool RN2xx3Interface::modemReceiving() {
    // SX1276 RegModemStat: detect/sync/RX/header-valid bits. This checks an
    // existing reception, not an invented CAD result or a last-packet RSSI.
    auto value = uart->command("radio get reg 18");
    if (value.empty() || value.size() > 2 || value.find_first_not_of("0123456789abcdefABCDEF") != std::string::npos)
        return true; // Fail closed if the modem cannot report activity.
    LOG_DEBUG("RN2903 modem status=0x%s",value.c_str());
    // On this stock modem idle continuous RX reports 0x04. Treating that
    // RX-on bit as a packet in progress would prevent every transmission.
    // Defer on signal detection/synchronization instead; header-valid can be
    // stale after a packet. This remains a best-effort check, not true CAD.
    return (std::stoul(value,nullptr,16) & 0x03) != 0;
}

void RN2xx3Interface::finishSend(bool success) {
    if (sendingPacket) {
        if (success) {
            ++txGood;
            LOG_INFO("RN2903 modem TX completed (%u)",txGood);
            airTime->logAirtime(TX_LOG,RadioInterface::getPacketTime(sendingPacket));
        } else ++txBad;
        auto *packet = sendingPacket; sendingPacket = nullptr; packetPool.release(packet);
    }
    txDeadline = 0; receiving = false; startReceive(); schedule();
}

void RN2xx3Interface::acceptPacket(const std::string &hex) {
    receiving = false;
    if (!configured) { ++rxBad; return; }
    if (hex.size() % 2 || hex.size() < 2*MESHTASTIC_HEADER_LENGTH || hex.size() > 2*MAX_LORA_PAYLOAD_LEN ||
        hex.find_first_not_of("0123456789abcdefABCDEF") != std::string::npos) { ++rxBad; startReceive(); return; }
    const auto length = hex.size()/2;
    auto *bytes = reinterpret_cast<uint8_t *>(&radioBuffer);
    for (size_t i=0;i<length;++i) bytes[i] = std::stoul(hex.substr(i*2,2),nullptr,16);
    if (!radioBuffer.header.from) { ++rxBad; startReceive(); return; } // Preserve upstream's remote-admin boundary.
    auto *packet = packetPool.allocZeroed();
    packet->from = radioBuffer.header.from; packet->to = radioBuffer.header.to;
    packet->id = radioBuffer.header.id; packet->channel = radioBuffer.header.channel;
    packet->hop_limit = radioBuffer.header.flags & PACKET_FLAGS_HOP_LIMIT_MASK;
    packet->hop_start = (radioBuffer.header.flags & PACKET_FLAGS_HOP_START_MASK) >> PACKET_FLAGS_HOP_START_SHIFT;
    packet->want_ack = !!(radioBuffer.header.flags & PACKET_FLAGS_WANT_ACK_MASK);
    packet->via_mqtt = !!(radioBuffer.header.flags & PACKET_FLAGS_VIA_MQTT_MASK);
    packet->next_hop = packet->hop_start ? radioBuffer.header.next_hop : NO_NEXT_HOP_PREFERENCE;
    packet->relay_node = packet->hop_start ? radioBuffer.header.relay_node : NO_RELAY_NODE;
    packet->rx_snr = std::stof(uart->command("radio get snr"));
    packet->rx_rssi = std::stoi(uart->command("radio get rssi"));
    packet->which_payload_variant = meshtastic_MeshPacket_encrypted_tag;
    packet->encrypted.size = length-MESHTASTIC_HEADER_LENGTH;
    std::memcpy(packet->encrypted.bytes,radioBuffer.payload,packet->encrypted.size);
    ++rxGood;
    LOG_INFO("RN2903 RF frame received: bytes=%u (%u)",static_cast<unsigned>(length),rxGood);
    airTime->logAirtime(RX_LOG,getPacketTime(static_cast<uint32_t>(length),true));
    startReceive();
    deliverToReceiver(packet); // Standard Meshtastic authentication/decryption follows.
}

int32_t RN2xx3Interface::runOnce() {
    std::lock_guard<std::recursive_mutex> guard(mutex);
    if (!ready || disabled) return 20;
    try {
        for (int budget=0;budget<32;++budget) {
            auto event = uart->event(); if (event.empty()) break;
            if (event.rfind("radio_rx ",0) == 0) acceptPacket(event.substr(9));
            else if (event == "radio_tx_ok") finishSend(true);
            else if (event == "radio_err") {
                if (sendingPacket) finishSend(false);
                else { ++rxBad; receiving = false; startReceive(); }
            }
        }
        if (sendingPacket && static_cast<int32_t>(millis()-txDeadline) >= 0) finishSend(false);
        if (pendingProfile && !sendingPacket) {
            pendingProfile = false;
            stopReceive();
            RadioInterface::reconfigure();
            applyProfile();
        }
        if (!configured) return 20;
        startReceive();
        if (!sendingPacket && !queue.empty() && due && static_cast<int32_t>(millis()-due) >= 0) {
            if (modemReceiving()) { due = millis()+getTxDelayMsec(); return 20; }
            stopReceive();
            const auto *front = queue.getFront();
            if (!transmitAllowed || !config.lora.tx_enabled || config.lora.region != meshtastic_Config_LoRaConfig_RegionCode_US || !front) {
                while (!queue.empty()) packetPool.release(queue.dequeue());
                due=0; startReceive(); return 20;
            }
            auto *packet = queue.dequeue(); due=0;
            const auto count = beginSending(packet);
            LOG_INFO("RN2903 modem TX starting: bytes=%u",static_cast<unsigned>(count));
            const char alphabet[] = "0123456789abcdef";
            const auto *raw = reinterpret_cast<const uint8_t *>(&radioBuffer);
            std::string hex; hex.reserve(count*2);
            for (size_t i=0;i<count;++i) { hex+=alphabet[raw[i]>>4]; hex+=alphabet[raw[i]&15]; }
            uart->requireOk("radio tx "+hex);
            lastTxStart = millis(); txDeadline = lastTxStart+getPacketTime(static_cast<uint32_t>(count))+5000;
        }
    } catch (...) {
        LOG_ERROR("RN2903 I/O failed; exiting so the supervisor can recover the USB connection");
        std::exit(EXIT_FAILURE);
    }
    return 20;
}

uint32_t RN2xx3Interface::getPacketTime(uint32_t length,bool received) {
    (void)received;
    const double symbolMs = std::pow(2.0,sf)/bw;
    const int lowDataRate = symbolMs > 16.0 ? 1 : 0;
    const double numerator = 8.0*length-4.0*sf+28.0+16.0; // CRC on, explicit header.
    const double symbols = 8.0+std::max(0.0,std::ceil(numerator/(4.0*(sf-2*lowDataRate)))*cr);
    return static_cast<uint32_t>(std::ceil((preambleLength+4.25+symbols)*symbolMs));
}
#endif
