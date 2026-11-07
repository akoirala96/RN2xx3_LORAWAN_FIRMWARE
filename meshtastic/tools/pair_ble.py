#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Verify a phone's Bluetooth pairing number locally; never auto-confirm it."""
import argparse, json, os
from pathlib import Path
import tempfile, time

p = argparse.ArgumentParser(description=__doc__)
p.add_argument(
    "--pairing", type=Path, default=Path.home() / ".local/share/rn2xx3/ble-pairing.json"
)
args = p.parse_args()
if args.pairing.parent.stat().st_mode & 0o077:
    raise SystemExit("Pairing directory must be private")
try:
    advertised = json.loads(args.pairing.read_text()).get("name", "LoStik")
except (FileNotFoundError, ValueError):
    advertised = "LoStik"
print("Waiting for pairing. Select " + str(advertised) + " on your phone.", flush=True)
end = time.monotonic() + 900
shown = set()
requested = False
while time.monotonic() < end:
    try:
        d = json.loads(args.pairing.read_text())
    except (FileNotFoundError, ValueError):
        time.sleep(0.2)
        continue
    if time.time() - d.get("updated_at", 0) > 30:
        time.sleep(0.2)
        continue
    status = d.get("status")
    code = d.get("passkey")
    marker = (status, code, d.get("token"))
    if status == "enter_this_pin_on_phone" and code and marker not in shown:
        shown.add(marker)
        requested = True
        print("Enter this PIN on your phone: " + code, flush=True)
        print("Leave this helper open until pairing completes.", flush=True)
    if (
        status == "confirm_number_matches_phone"
        and code
        and d.get("token")
        and marker not in shown
    ):
        shown.add(marker)
        requested = True
        print("Pi pairing number: " + code, flush=True)
        entered = input(
            "Type the six digits shown on your PHONE to confirm they match: "
        ).strip()
        if entered != code:
            raise SystemExit("Numbers differ. Pairing was NOT approved.")
        latest = json.loads(args.pairing.read_text())
        if latest.get("token") != d["token"] or time.time() - d["updated_at"] > 25:
            raise SystemExit(
                "Pairing expired; start this helper and select LoStik again"
            )
        target = args.pairing.with_suffix(".approved.json")
        fd, name = tempfile.mkstemp(prefix=".ble-approve-", dir=target.parent)
        try:
            with os.fdopen(fd, "w") as f:
                json.dump({"token": d["token"], "code": entered}, f)
            os.replace(name, target)
        finally:
            if os.path.exists(name):
                os.unlink(name)
        print(
            "Number matched. Confirm Pair on Android if it is still asking.", flush=True
        )
    if status == "pairing_cancelled" and marker not in shown:
        shown.add(marker)
        print(
            "Pairing was cancelled. Keep this helper open and select the device again.",
            flush=True,
        )
    if status == "paired" and requested:
        print(
            "Bluetooth pairing completed. Open Meshtastic and select the paired device.",
            flush=True,
        )
        raise SystemExit(0)
    time.sleep(0.2)
raise SystemExit("No pairing request received within fifteen minutes")
