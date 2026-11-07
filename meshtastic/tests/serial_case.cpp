// SPDX-License-Identifier: GPL-3.0-or-later
#include "../driver/RN2xx3Serial.h"
#include <iostream>

int main(int argc,char **argv) {
    if (argc != 3) return 2;
    try {
        rn2xx3::Serial port(argv[1]);
        const std::string mode = argv[2];
        if (mode == "interleave") {
            if (port.command("test",500) != "ok") return 3;
            if (port.event() != "radio_rx 01020304") return 4;
        } else if (mode == "timeout") {
            auto start = std::chrono::steady_clock::now();
            bool failed = false;
            try { port.command("test",100); } catch (const std::runtime_error &) { failed = true; }
            if (!failed || std::chrono::steady_clock::now()-start > std::chrono::seconds(1)) return 5;
        } else if (mode == "oversize") {
            bool failed = false;
            try { port.command("test",500); } catch (const std::runtime_error &) { failed = true; }
            if (!failed) return 6;
        } else if (mode == "exclusive") {
            bool failed = false;
            try { rn2xx3::Serial second(argv[1]); } catch (const std::runtime_error &) { failed = true; }
            if (!failed) return 7;
        } else if (mode == "newline") {
            bool failed = false;
            try { port.writeLine("test\r\nradio tx 00"); } catch (const std::runtime_error &) { failed = true; }
            if (!failed) return 8;
        } else if (mode == "bounded-events") {
            bool failed = false;
            try { port.command("test",500); } catch (const std::runtime_error &) { failed = true; }
            if (!failed) return 9;
        } else return 10;
        std::cout << "PASS " << mode << std::endl;
        return 0;
    } catch (const std::exception &e) { std::cerr << e.what() << std::endl; return 1; }
}
