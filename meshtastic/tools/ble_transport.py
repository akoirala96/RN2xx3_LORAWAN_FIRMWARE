# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded Meshtastic TCP framing and BLE mailbox; no radio simulation."""
from collections import deque

MAX_FRAME = 512


def encode(payload):
    if not 0 < len(payload) <= MAX_FRAME:
        raise ValueError("Invalid ToRadio length")
    return b"\x94\xc3" + len(payload).to_bytes(2, "big") + payload


class Decoder:
    def __init__(self):
        self.buffer = bytearray()

    def feed(self, data):
        self.buffer.extend(data)
        if len(self.buffer) > 8192:
            raise ValueError("TCP framing buffer exceeded")
        frames = []
        while len(self.buffer) >= 4:
            if self.buffer[:2] != b"\x94\xc3":
                raise ValueError("Unexpected native API framing")
            size = int.from_bytes(self.buffer[2:4], "big")
            if size > MAX_FRAME:
                raise ValueError("Native API frame exceeded")
            if len(self.buffer) < size + 4:
                break
            if size:
                frames.append(bytes(self.buffer[4 : size + 4]))
            del self.buffer[: size + 4]
        return frames


class Mailbox:
    def __init__(self, capacity=512):
        self.capacity = capacity
        self.queue = deque()
        self.last = b""
        self.number = 0

    def push(self, payload):
        if not 0 < len(payload) <= MAX_FRAME:
            raise ValueError("Invalid FromRadio length")
        if len(self.queue) >= self.capacity:
            raise BufferError("BLE reader is not draining its queue")
        self.queue.append(bytes(payload))
        self.number = (self.number + 1) & 0xFFFFFFFF

    def read(self, offset=0):
        if offset == 0:
            self.last = self.queue.popleft() if self.queue else b""
        if not 0 <= offset <= len(self.last):
            raise ValueError("Invalid BLE read offset")
        return self.last[offset:]

    def clear(self):
        self.queue.clear()
        self.last = b""
        self.number = 0
