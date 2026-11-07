# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from ble_transport import Decoder, Mailbox, encode


class TransportTests(unittest.TestCase):
    def test_fragmented_native_stream_keeps_protobuf_boundaries(self):
        d = Decoder()
        raw = encode(b"first") + encode(b"second")
        frames = []
        for b in raw:
            frames.extend(d.feed(bytes([b])))
        self.assertEqual(frames, [b"first", b"second"])

    def test_oversized_and_invalid_frames_fail_closed(self):
        for raw in [b"\x94\xc3\xff\xff", b"bad!"]:
            with self.assertRaises(ValueError):
                Decoder().feed(raw)
        with self.assertRaises(ValueError):
            encode(b"x" * 513)

    def test_empty_reads_and_long_read_offsets_do_not_consume_next_packet(self):
        q = Mailbox()
        q.push(b"first")
        q.push(b"second")
        self.assertEqual(q.read(), b"first")
        self.assertEqual(q.read(2), b"rst")
        self.assertEqual(q.read(), b"second")
        self.assertEqual(q.read(), b"")

    def test_slow_client_cannot_make_an_unbounded_queue(self):
        q = Mailbox(capacity=2)
        q.push(b"1")
        q.push(b"2")
        with self.assertRaises(BufferError):
            q.push(b"3")

    def test_disconnect_clears_previous_clients_private_packets(self):
        q = Mailbox()
        q.push(b"private")
        q.read()
        q.push(b"pending")
        q.clear()
        self.assertEqual(q.read(), b"")
        self.assertEqual(q.number, 0)

    def test_counter_is_four_byte_unsigned(self):
        q = Mailbox()
        q.number = 0xFFFFFFFF
        q.push(b"next")
        self.assertEqual(q.number.to_bytes(4, "little"), b"\0\0\0\0")


if __name__ == "__main__":
    unittest.main()
