#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise real termios/PTY framing, response races and failure boundaries."""
import os
from pathlib import Path
import pty
import select
import shlex
import subprocess
import tempfile
import threading
import time
import unittest


class UartTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.binary = Path(cls.temp.name) / "serial-case"
        command = [
            os.getenv("CXX", "g++"),
            "-std=c++17",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-O1",
            *shlex.split(os.getenv("CXXFLAGS", "")),
            str(Path(__file__).with_name("serial_case.cpp")),
            "-o",
            str(cls.binary),
        ]
        subprocess.run(command, check=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def run_case(self, mode, chunks=()):
        master, slave = pty.openpty()
        path = os.ttyname(slave)
        stop = threading.Event()
        commands = []
        errors = []

        def modem():
            buffer = b""
            try:
                while not stop.is_set():
                    if not select.select([master], [], [], 0.02)[0]:
                        continue
                    buffer += os.read(master, 1024)
                    if b"\r\n" in buffer:
                        command, buffer = buffer.split(b"\r\n", 1)
                        commands.append(command)
                        for chunk in chunks:
                            os.write(master, chunk)
                            time.sleep(0.005)
            except OSError as exc:
                if not stop.is_set():
                    errors.append(exc)

        thread = threading.Thread(target=modem, daemon=True)
        thread.start()
        try:
            result = subprocess.run(
                [str(self.binary), path, mode],
                capture_output=True,
                text=True,
                timeout=3,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("PASS " + mode, result.stdout)
            self.assertFalse(errors)
            self.assertEqual(
                commands, [] if mode in {"exclusive", "newline"} else [b"test"]
            )
        finally:
            stop.set()
            thread.join(timeout=1)
            os.close(master)
            os.close(slave)

    def test_partial_reply_preserves_interleaved_radio_packet(self):
        self.run_case("interleave", [b"radio_rx 0102", b"0304\r\n", b"o", b"k\r\n"])

    def test_timeout_is_bounded(self):
        self.run_case("timeout")

    def test_rn2903_105_double_space_receive_formatter(self):
        self.run_case("interleave", [b"radio_rx  01020304\r\n", b"ok\r\n"])

    def test_receive_separator_whitespace_is_normalized(self):
        self.run_case("interleave", [b"radio_rx\t 01020304 \t\r\n", b"ok\r\n"])

    def test_oversized_unterminated_line_is_rejected(self):
        self.run_case("oversize", [b"x" * 1200])

    def test_oversized_terminated_line_is_rejected(self):
        self.run_case("oversize", [b"x" * 1200 + b"\r\n"])

    def test_second_uart_owner_is_rejected(self):
        self.run_case("exclusive")

    def test_command_line_injection_is_rejected_before_write(self):
        self.run_case("newline")

    def test_unsolicited_event_queue_is_bounded(self):
        self.run_case("bounded-events", [b"radio_err\r\n" * 40 + b"ok\r\n"])


if __name__ == "__main__":
    unittest.main()
