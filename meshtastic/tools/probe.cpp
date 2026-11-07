// SPDX-License-Identifier: GPL-3.0-or-later
// No transmit operation exists in this tool. RX profile changes are temporary.
#include "../driver/RN2xx3Serial.h"
#include <iostream>
#include <map>
#include <thread>
#include <vector>

int main(int argc, char **argv) {
    if (argc < 2 || argc > 3) { std::cerr << "Usage: probe DEVICE [--rx-us-longfast]\n"; return 2; }
    bool receive = argc == 3 && std::string(argv[2]) == "--rx-us-longfast";
    if (argc == 3 && !receive) return 2;
    try {
        rn2xx3::Serial port(argv[1]);
        while (!port.readLine(0).empty()) {}
        auto version = port.command("sys get ver");
        std::cout << "Firmware: " << version << std::endl;
        if (version.rfind("RN2903 ", 0) != 0) throw std::runtime_error("This prototype requires an RN2903 US radio");
        const std::vector<std::string> fields{"mod","freq","pwr","sf","bw","cr","crc","iqi","prlen","sync","wdt"};
        std::map<std::string,std::string> original;
        for (auto &name : fields) {
            original[name] = port.command("radio get " + name);
            if (original[name].empty() || original[name] == "invalid_param") throw std::runtime_error("Required radio control unavailable");
        }
        std::cout << "All required radio controls readable; no persistent settings written\n";
        if (!receive) return 0;
        auto status = port.command("mac get status");
        if (std::stoul(status, nullptr, 16) & 1) throw std::runtime_error("Preserve an active LoRaWAN join before testing");
        bool paused = false;
        auto restore = [&]() {
            if (!paused) return;
            port.command("radio rxstop");
            while (!port.event().empty()) {}
            for (auto &name : fields) port.requireOk("radio set " + name + " " + original[name]);
            port.requireOk("mac resume");
            paused = false;
            std::cout << "Original radio profile restored; transmissions: 0\n";
        };
        try {
            const auto pause = port.command("mac pause");
            if (pause.empty() || pause.find_first_not_of("0123456789") != std::string::npos)
                throw std::runtime_error("Cannot pause LoRaWAN for raw reception");
            paused = true;
            const std::vector<std::pair<std::string,std::string>> profile{
                {"mod","lora"},{"freq","906875000"},{"sf","sf11"},{"bw","250"},{"cr","4/5"},
                {"crc","on"},{"iqi","off"},{"prlen","16"},{"sync","2b"},{"wdt","0"}};
            for (auto &[name,value] : profile) {
                port.requireOk("radio set " + name + " " + value);
                auto actual = port.command("radio get " + name);
                std::transform(actual.begin(),actual.end(),actual.begin(),[](unsigned char c){return std::tolower(c);});
                if (actual != value) throw std::runtime_error("Radio profile verification failed");
            }
            std::cout << "US LongFast raw profile verified; sync=2b, SF11, BW250, CR4/5, 906875000 Hz\n";
            std::cout << "Modem-status register readable: " << port.command("radio get reg 18") << std::endl;
            port.requireOk("radio rx 0");
            size_t packets = 0, errors = 0;
            auto until = std::chrono::steady_clock::now() + std::chrono::seconds(45);
            while (std::chrono::steady_clock::now() < until) {
                auto event = port.event();
                if (event.rfind("radio_rx ",0) == 0) { ++packets; port.requireOk("radio rx 0"); }
                else if (event == "radio_err") { ++errors; port.requireOk("radio rx 0"); }
                else std::this_thread::sleep_for(std::chrono::milliseconds(10));
            }
            std::cout << "RX packets: " << packets << "; RX errors: " << errors << std::endl;
            restore();
        } catch (...) { try { restore(); } catch (...) { std::cerr << "Profile restore failed; unplug/replug to restore saved settings\n"; } throw; }
        return 0;
    } catch (const std::exception &error) { std::cerr << error.what() << std::endl; return 1; }
}
