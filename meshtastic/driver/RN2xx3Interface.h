// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#if defined(ARCH_PORTDUINO)
#include "RadioInterface.h"
#include "MeshPacketQueue.h"
#include "RN2xx3Serial.h"
#include "concurrency/OSThread.h"
#include <map>
#include <memory>
#include <mutex>

// Meshtastic owns encryption, routing, ACKs, deduplication and the client API.
// This adapter supplies the radio operations of an unmodified RN2903 modem.
class RN2xx3Interface : public RadioInterface, protected concurrency::OSThread {
    std::unique_ptr<rn2xx3::Serial> uart;
    MeshPacketQueue queue{MAX_TX_QUEUE};
    std::recursive_mutex mutex;
    std::string path;
    std::map<std::string,std::string> original;
    bool ready = false, paused = false, receiving = false, configured = false;
    bool transmitAllowed = false;
    bool pendingProfile = false;
    uint32_t due = 0, txDeadline = 0;
    uint32_t rxGood = 0, rxBad = 0, txGood = 0, txBad = 0;
    bool applyProfile();
    void startReceive();
    void stopReceive();
    void acceptPacket(const std::string &hex);
    void finishSend(bool success);
    bool modemReceiving();
    void schedule();
  protected:
    int32_t runOnce() override;
  public:
    explicit RN2xx3Interface(const std::string &device);
    ~RN2xx3Interface() override;
    bool init() override;
    bool reconfigure() override;
    bool sleep() override;
    bool canSleep() override { return queue.empty() && !sendingPacket; }
    ErrorCode send(meshtastic_MeshPacket *packet) override;
    meshtastic_QueueStatus getQueueStatus() override;
    bool cancelSending(NodeNum from, PacketId id) override;
    bool findInTxQueue(NodeNum from, PacketId id) override;
    uint32_t getPacketTime(uint32_t length, bool received = false) override;
};
#endif
