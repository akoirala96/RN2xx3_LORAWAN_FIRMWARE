#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Provision this US-only adapter over its local Meshtastic client API."""
import argparse
import logging
import time


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=4403)
    p.add_argument("--region", choices=["US"], required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--short-name", required=True)
    p.add_argument("--power", type=int, choices=range(2, 18), default=2)
    args = p.parse_args()
    if (
        not 1 <= len(args.name.encode()) <= 39
        or not 1 <= len(args.short_name.encode()) <= 4
    ):
        p.error("Name must fit 39 UTF-8 bytes and short name 4 bytes")
    logging.disable(logging.CRITICAL)
    from meshtastic.tcp_interface import TCPInterface
    from meshtastic.protobuf import config_pb2

    x = TCPInterface(hostname=args.host, portNumber=args.port)
    c = x.localNode.localConfig.lora
    c.region = config_pb2.Config.LoRaConfig.US
    c.use_preset = True
    c.modem_preset = config_pb2.Config.LoRaConfig.LONG_FAST
    c.coding_rate = 5
    c.tx_power = args.power
    c.tx_enabled = True
    c.channel_num = 0
    c.override_frequency = 0
    c.frequency_offset = 0
    x.localNode.writeConfig("lora")
    time.sleep(2)
    x.close()
    time.sleep(8)
    for attempt in range(12):
        try:
            x = TCPInterface(hostname=args.host, portNumber=args.port)
            c = x.localNode.localConfig.lora
            if (
                c.region != 1
                or not c.use_preset
                or c.modem_preset != 0
                or c.tx_power != args.power
            ):
                raise RuntimeError("Configuration readback differs")
            x.localNode.setOwner(long_name=args.name, short_name=args.short_name)
            time.sleep(2)
            node = x.myInfo.my_node_num
            x.close()
            print(
                "Provisioned !%08x: US LongFast, CR4/5, %d dBm. Hardware TX gate is controlled separately."
                % (node, args.power)
            )
            return
        except Exception:
            if attempt == 11:
                raise SystemExit(
                    "Could not verify configuration; leave TX disabled and inspect the service"
                )
            time.sleep(5)


if __name__ == "__main__":
    main()
