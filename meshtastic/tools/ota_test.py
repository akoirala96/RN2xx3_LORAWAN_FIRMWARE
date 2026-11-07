#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Opt-in, real radio test between two local/SSH-forwarded Meshtastic APIs."""
import argparse
import json
import logging
import secrets
import threading
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--a-port", type=int, default=4403)
    parser.add_argument("--b-port", type=int, default=4404)
    parser.add_argument("--antennas-attached", action="store_true")
    parser.add_argument("--send", action="store_true")
    parser.add_argument("--timeout", type=int, default=75)
    args = parser.parse_args()
    if not args.antennas_attached or not args.send:
        parser.error(
            "Attach both antennas and explicitly pass --antennas-attached --send to transmit"
        )
    logging.disable(logging.CRITICAL)
    from meshtastic.tcp_interface import TCPInterface
    from meshtastic.protobuf import mesh_pb2, portnums_pb2
    from google.protobuf.json_format import ParseDict
    from pubsub import pub

    a = TCPInterface(hostname="127.0.0.1", portNumber=args.a_port)
    b = TCPInterface(hostname="127.0.0.1", portNumber=args.b_port)
    if a.myInfo.my_node_num == b.myInfo.my_node_num:
        raise SystemExit("Test requires two distinct radio nodes")
    received = []
    lock = threading.Lock()
    signal = threading.Event()

    def observe(packet, interface):
        if interface not in (a, b):
            return
        with lock:
            received.append((interface, packet))
            signal.set()

    pub.subscribe(observe, "meshtastic.receive")

    def wait_for(predicate):
        end = time.monotonic() + args.timeout
        while time.monotonic() < end:
            with lock:
                for interface, packet in received:
                    if predicate(interface, packet):
                        return packet
                signal.clear()
            signal.wait(min(1, max(0, end - time.monotonic())))
        return None

    def label(x):
        return "!%08x" % x.myInfo.my_node_num

    results = []
    try:
        # Standard upstream node information, including public key material,
        # allows subsequent directed messages to use the native PKI policy.
        for source, target in [(a, b), (b, a)]:
            user = mesh_pb2.User()
            ParseDict(source.getMyNodeInfo()["user"], user, ignore_unknown_fields=True)
            source.sendData(
                user,
                portNum=portnums_pb2.PortNum.NODEINFO_APP,
                destinationId="^all",
                wantAck=False,
                wantResponse=False,
            )
            packet = wait_for(
                lambda i, p: i is target
                and p.get("from") == source.myInfo.my_node_num
                and p.get("decoded", {}).get("portnum") == "NODEINFO_APP"
            )
            result = {
                "test": "node-info",
                "from": label(source),
                "to": label(target),
                "received": bool(packet),
            }
            results.append(result)
            print(json.dumps(result), flush=True)
            if not packet:
                raise RuntimeError(
                    "Peer node information was not received over the air"
                )
            time.sleep(3)
        nonce = secrets.token_hex(6)
        for index, (source, target) in enumerate([(a, b), (b, a)]):
            text = "RN2xx3 OTA test " + nonce + " " + str(index)
            outgoing = source.sendText(
                text, destinationId=label(target), wantAck=True, channelIndex=0
            )
            packet = wait_for(
                lambda i, p: i is target
                and p.get("from") == source.myInfo.my_node_num
                and p.get("decoded", {}).get("text") == text
            )
            ack = wait_for(
                lambda i, p: i is source
                and p.get("from") == target.myInfo.my_node_num
                and p.get("decoded", {}).get("requestId") == outgoing.id
                and p.get("decoded", {}).get("portnum") == "ROUTING_APP"
            )
            routing = ack.get("decoded", {}).get("routing", {}) if ack else {}
            reason = routing.get("errorReason", "NONE")
            result = {
                "test": "directed-text",
                "from": label(source),
                "to": label(target),
                "received": bool(packet),
                "ack_received": bool(ack),
                "ack_success": bool(ack) and reason in ("NONE", 0),
                "rssi": packet.get("rxRssi") if packet else None,
                "snr": packet.get("rxSnr") if packet else None,
                "via_mqtt": bool(packet.get("viaMqtt")) if packet else None,
            }
            results.append(result)
            print(json.dumps(result), flush=True)
            if not packet or not result["ack_success"] or result["via_mqtt"]:
                raise RuntimeError("Directed radio delivery or acknowledgment failed")
            time.sleep(3)
        print(
            json.dumps(
                {
                    "passed": True,
                    "checks": len(results),
                    "independent_stock_radio_tested": False,
                }
            ),
            flush=True,
        )
    finally:
        pub.unsubscribe(observe, "meshtastic.receive")
        a.close()
        b.close()


if __name__ == "__main__":
    main()
