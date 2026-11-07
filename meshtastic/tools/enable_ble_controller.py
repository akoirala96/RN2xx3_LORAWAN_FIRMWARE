#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Configure a dedicated Pi for LE-only bonding and BlueZ-managed privacy."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--apply", action="store_true")
args = p.parse_args()
config = Path("/etc/bluetooth/main.conf")
backup = Path("/var/lib/rn2xx3/main.conf.before-rn2xx3-ble")
print(
    "Set /etc/bluetooth/main.conf [General] ControllerMode=le and Privacy=device; "
    "stop BlueZ, power off hci0, then start BlueZ so it configures privacy before power-on."
)
if not args.apply:
    raise SystemExit("Preview only. Use sudo and --apply to apply this configuration.")
if os.geteuid() != 0:
    raise SystemExit(
        "Administrator rights are required for the Bluetooth controller configuration"
    )
manager = shutil.which("btmgmt")
if not manager:
    raise SystemExit("Install the BlueZ btmgmt utility before applying this setup")
if config.is_symlink():
    raise SystemExit("Refusing a symlinked Bluetooth configuration")
text = config.read_text()
lines = text.splitlines(keepends=True)
output = []
inside = False
found = set()
general = False
settings = {"controllermode": "ControllerMode=le\n", "privacy": "Privacy=device\n"}
for line in lines:
    stripped = line.strip()
    if stripped.startswith("["):
        if inside:
            output.extend(v for k, v in settings.items() if k not in found)
        inside = stripped.lower() == "[general]"
        general = general or inside
    key = stripped.split("=", 1)[0].strip().lower() if "=" in stripped else ""
    if inside and key in settings and not stripped.startswith(("#", ";")):
        if key not in found:
            output.append(settings[key])
            found.add(key)
    else:
        output.append(line)
if inside:
    if output and not output[-1].endswith("\n"):
        output[-1] += "\n"
    output.extend(v for k, v in settings.items() if k not in found)
if not general:
    output = ["[General]\n", *settings.values(), "\n", *output]
backup.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
if not backup.exists():
    shutil.copyfile(config, backup)
    backup.chmod(0o600)
fd, name = tempfile.mkstemp(prefix=".rn2xx3-", dir=config.parent)
try:
    with os.fdopen(fd, "w") as f:
        f.write("".join(output))
    os.chmod(name, config.stat().st_mode & 0o777)
    os.replace(name, config)
finally:
    if os.path.exists(name):
        os.unlink(name)
subprocess.run(["systemctl", "stop", "bluetooth.service"], check=True)
try:
    subprocess.run(
        [manager, "--index", "0", "power", "off"], check=True, capture_output=True
    )
finally:
    subprocess.run(["systemctl", "start", "bluetooth.service"], check=True)
print(
    "LE-only Bluetooth with managed privacy configured. Original configuration: "
    + str(backup)
)
