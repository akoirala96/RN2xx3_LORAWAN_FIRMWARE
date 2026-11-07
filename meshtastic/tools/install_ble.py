#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Add the authenticated Android/iOS BLE transport to a native Pi install."""
import argparse, json, os
import re
from pathlib import Path
import shutil, subprocess

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--directory", type=Path, default=Path.home() / ".local/share/rn2xx3")
p.add_argument("--name", default="LoStik", help="Short Bluetooth advertising name")
args = p.parse_args()
if not re.fullmatch(r"[A-Za-z0-9_-]{1,7}", args.name):
    p.error("Bluetooth name must contain 1–7 letters, digits, underscores or dashes")
base = args.directory.resolve()
if os.geteuid() == 0:
    raise SystemExit("Run as the regular radio user")
if any(c in str(base) for c in "\r\n%$ \"'"):
    raise SystemExit("Use a plain installation path")
if not (base / "venv/bin/python").is_file() or not (base / "config.yaml").is_file():
    raise SystemExit("Install the native node first")
if base.stat().st_mode & 0o077:
    raise SystemExit("The installation directory must be private (0700)")
subprocess.run(
    [
        str(base / "venv/bin/pip"),
        "install",
        "--disable-pip-version-check",
        "dbus-next==0.2.3",
    ],
    check=True,
)
source = Path(__file__).resolve().parent
for name in ["ble_bridge.py", "ble_transport.py", "portable_ui.py", "pair_ble.py"]:
    shutil.copyfile(source / name, base / name)
state = base / "ble-pairing.json"
if not state.exists():
    fd = os.open(state, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"name": args.name, "status": "not_started", "passkey": None}, f)
unit = """[Unit]
Description=Meshtastic Bluetooth app transport
After=rn2xx3-radio.service
[Service]
Type=simple
WorkingDirectory=BASE
ExecStart=BASE/venv/bin/python BASE/ble_bridge.py --pairing BASE/ble-pairing.json --lease BASE/ble-active --name BLE_NAME
Restart=always
RestartSec=15
UMask=0077
NoNewPrivileges=true
MemoryMax=96M
StandardOutput=append:BASE/ble-service.log
StandardError=append:BASE/ble-service.log
[Install]
WantedBy=default.target
""".replace(
    "BASE", str(base)
).replace(
    "BLE_NAME", args.name
)
path = Path.home() / ".config/systemd/user/rn2xx3-ble.service"
if path.exists():
    raise SystemExit(
        "Existing BLE unit preserved; update its source and restart it explicitly"
    )
fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
with os.fdopen(fd, "w") as f:
    f.write(unit)
subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
subprocess.run(
    ["systemctl", "--user", "enable", "--now", "rn2xx3-ble.service"], check=True
)
print(
    "BLE transport installed. Pairing status and current generated PIN: " + str(state)
)
print("A new pairing window lasts three minutes; restart rn2xx3-ble to reopen it.")
