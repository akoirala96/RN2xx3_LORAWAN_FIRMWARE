#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Create private portable-node configuration. Does not open or transmit on USB."""
import argparse
import os
from pathlib import Path
import secrets
import stat


def configure(directory, device, uid, gid, device_gid):
    directory = Path(directory)
    env = directory / ".env"
    config = directory / "config.yaml"
    if env.exists() or config.exists():
        if env.exists() and config.exists():
            return False
        raise ValueError(
            "Incomplete existing setup; preserve and inspect it before continuing"
        )
    mac = bytearray(secrets.token_bytes(6))
    mac[0] = (mac[0] & 0xFC) | 0x02
    if mac[2:] == b"\0\0\0\0":
        mac[-1] = 1
    text = (
        '''Lora:
  Module: auto
General:
  MACAddress: "'''
        + ":".join(f"{b:02x}" for b in mac)
        + """"
  TCPPort: 4403
  MaxNodes: 200
  MaxMessageQueue: 100
  ConfigDirectory: /etc/meshtasticd/config.d/
  AvailableDirectory: /etc/meshtasticd/available.d/
HostMetrics:
  ReportInterval: 0
Logging:
  LogLevel: warn
Webserver:
  Port: -1
"""
    )
    values = {
        "RADIO_DEVICE": str(device),
        "RADIO_DEVICE_GID": str(device_gid),
        "RUN_UID": str(uid),
        "RUN_GID": str(gid),
        "RADIO_TX_ENABLED": "0",
        "NODE_API_PORT": "4403",
        "UI_PORT": "8080",
        "UI_ADMIN_USERNAME": "meshadmin",
        "UI_ADMIN_PASSWORD": secrets.token_urlsafe(32),
        "UI_SESSION_SECRET": secrets.token_urlsafe(64),
    }
    for name in ["data", "data/node", "data/ui"]:
        path = directory / name
        path.mkdir(parents=True, exist_ok=True)
        path.chmod(0o700)
    # O_EXCL also protects against accidental concurrent setup overwrites.
    for path, content in [
        (config, text),
        (env, "".join(k + "=" + v + "\n" for k, v in values.items())),
    ]:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(content)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", type=Path, required=True)
    parser.add_argument(
        "--directory", type=Path, default=Path(__file__).resolve().parent.parent
    )
    args = parser.parse_args()
    if os.geteuid() == 0:
        parser.error(
            "Run setup as your regular Docker user so private files keep that ownership"
        )
    device = args.device.absolute()
    if any(c in str(device) for c in "\r\n\x00=$'\" "):
        parser.error("Use a plain /dev path without shell or env-file syntax")
    if not str(device).startswith("/dev/") or not stat.S_ISCHR(device.stat().st_mode):
        parser.error("Select the radio character device under /dev")
    created = configure(
        args.directory, device, os.getuid(), os.getgid(), device.stat().st_gid
    )
    print(
        "Private configuration created; TX is disabled. UI login is saved in .env."
        if created
        else "Existing private configuration preserved."
    )


if __name__ == "__main__":
    main()
