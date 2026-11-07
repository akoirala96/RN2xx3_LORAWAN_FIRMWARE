// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <algorithm>
#include <cerrno>
#include <chrono>
#include <cctype>
#include <deque>
#include <fcntl.h>
#include <poll.h>
#include <stdexcept>
#include <string>
#include <sys/file.h>
#include <sys/ioctl.h>
#include <termios.h>
#include <unistd.h>

namespace rn2xx3 {
// A bounded, exclusively-owned UART transport. Unsolicited RF events must not
// be mistaken for command replies when a packet arrives during a command.
class Serial {
    int fd = -1;
    std::string buffer;
    std::deque<std::string> events;
    static constexpr size_t maxLine = 1024;
  public:
    explicit Serial(const std::string &path) {
        fd = ::open(path.c_str(), O_RDWR | O_NOCTTY | O_NONBLOCK | O_CLOEXEC);
        if (fd < 0) throw std::runtime_error("Cannot open RN2xx3 UART");
        try {
            if (flock(fd, LOCK_EX | LOCK_NB) < 0) throw std::runtime_error("RN2xx3 UART is already owned");
            termios t{};
            if (tcgetattr(fd, &t) < 0) throw std::runtime_error("Cannot read UART settings");
            cfmakeraw(&t);
            cfsetispeed(&t, B57600); cfsetospeed(&t, B57600);
            t.c_cflag |= CLOCAL | CREAD;
            t.c_cflag &= ~(HUPCL | CRTSCTS);
            t.c_cc[VMIN] = 0; t.c_cc[VTIME] = 0;
            if (tcsetattr(fd, TCSANOW, &t) < 0) throw std::runtime_error("Cannot configure RN2xx3 UART");
            int modem = 0;
            if (ioctl(fd, TIOCMGET, &modem) == 0) {
                modem &= ~(TIOCM_DTR | TIOCM_RTS);
                ioctl(fd, TIOCMSET, &modem);
            }
        } catch (...) { ::close(fd); fd = -1; throw; }
    }
    ~Serial() { if (fd >= 0) ::close(fd); }
    Serial(const Serial &) = delete;
    Serial &operator=(const Serial &) = delete;

    static bool isEvent(const std::string &line) {
        return (line.size()>8 && line.compare(0,8,"radio_rx")==0 &&
                (line[8]==' ' || line[8]=='\t')) || line == "radio_err" || line == "radio_tx_ok";
    }
    static std::string normalizeEvent(std::string line) {
        if (line.size()>8 && line.compare(0,8,"radio_rx")==0 &&
            (line[8]==' ' || line[8]=='\t')) {
            // RN2903 1.0.5's status string already ends in a space, and its
            // receive formatter adds another before the hex payload.
            const auto first=line.find_first_not_of(" \t",8);
            if (first==std::string::npos) return "radio_rx ";
            const auto last=line.find_last_not_of(" \t");
            return "radio_rx "+line.substr(first,last-first+1);
        }
        return line;
    }
    std::string readLine(int timeoutMs) {
        const auto until = std::chrono::steady_clock::now() + std::chrono::milliseconds(timeoutMs);
        for (;;) {
            auto newline = buffer.find('\n');
            if (newline != std::string::npos) {
                if (newline > maxLine) { buffer.clear(); throw std::runtime_error("Oversized RN2xx3 response"); }
                auto line = buffer.substr(0, newline); buffer.erase(0, newline + 1);
                if (!line.empty() && line.back() == '\r') line.pop_back();
                if (!line.empty()) return line;
                continue;
            }
            if (buffer.size() > maxLine) { buffer.clear(); throw std::runtime_error("Oversized RN2xx3 response"); }
            int remaining = std::max(0, static_cast<int>(std::chrono::duration_cast<std::chrono::milliseconds>(until - std::chrono::steady_clock::now()).count()));
            pollfd p{fd, POLLIN, 0};
            int rc = poll(&p, 1, remaining);
            if (rc < 0 && errno == EINTR) continue;
            if (rc < 0 || (p.revents & (POLLERR | POLLHUP | POLLNVAL))) throw std::runtime_error("RN2xx3 UART disconnected");
            if (!rc) return {};
            char bytes[512]; auto count = ::read(fd, bytes, sizeof(bytes));
            if (count > 0) buffer.append(bytes, count);
            else if (count < 0 && errno != EAGAIN && errno != EINTR) throw std::runtime_error("RN2xx3 UART read failed");
        }
    }
    void writeLine(const std::string &command) {
        if (command.empty() || command.size() > maxLine || command.find_first_of("\r\n") != std::string::npos)
            throw std::runtime_error("Invalid RN2xx3 command");
        const auto text = command + "\r\n";
        size_t offset = 0;
        const auto until = std::chrono::steady_clock::now() + std::chrono::seconds(2);
        while (offset < text.size()) {
            auto count = ::write(fd, text.data() + offset, text.size() - offset);
            if (count > 0) offset += count;
            else if (count < 0 && errno != EAGAIN && errno != EINTR) throw std::runtime_error("RN2xx3 UART write failed");
            if (std::chrono::steady_clock::now() >= until) throw std::runtime_error("RN2xx3 UART write timeout");
            if (offset < text.size()) { pollfd p{fd, POLLOUT, 0}; poll(&p, 1, 10); }
        }
    }
    std::string command(const std::string &text, int timeoutMs = 2000) {
        writeLine(text);
        const auto until = std::chrono::steady_clock::now() + std::chrono::milliseconds(timeoutMs);
        while (std::chrono::steady_clock::now() < until) {
            const auto remaining = std::chrono::duration_cast<std::chrono::milliseconds>(until - std::chrono::steady_clock::now()).count();
            auto line = readLine(std::max(1, static_cast<int>(remaining)));
            if (line.empty()) break;
            if (isEvent(line)) {
                if (events.size() >= 32) throw std::runtime_error("RN2xx3 event queue overflow");
                events.push_back(std::move(line));
            } else return line;
        }
        throw std::runtime_error("RN2xx3 command timeout");
    }
    void requireOk(const std::string &text) {
        if (command(text) != "ok") throw std::runtime_error("RN2xx3 rejected radio configuration");
    }
    std::string event() {
        if (!events.empty()) { auto line = events.front(); events.pop_front(); return normalizeEvent(std::move(line)); }
        return normalizeEvent(readLine(0));
    }
};
}
