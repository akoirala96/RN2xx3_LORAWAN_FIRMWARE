#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Install a prepared native runtime and local UI as user services (no root)."""
import argparse
import hashlib
import json
import os
import platform
from pathlib import Path
import secrets
import shutil
import stat
import subprocess


def write_private(path, text):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(text)


def install(bundle, directory, device):
    directory = directory.resolve()
    device = device.absolute()
    bundle = bundle.resolve()
    if os.geteuid() == 0:
        raise ValueError("Run as the regular radio user")
    if any(c in str(directory) + str(device) for c in "\n\r\x00\"' $%"):
        raise ValueError("Use plain paths without spaces or unit-file syntax")
    if not stat.S_ISCHR(device.stat().st_mode) or not os.access(
        device, os.R_OK | os.W_OK
    ):
        raise ValueError(
            "Radio requires read/write device access (normally the dialout group)"
        )
    binary = bundle / "bin/meshtasticd"
    if not binary.is_file():
        raise ValueError("Bundle does not contain bin/meshtasticd")
    manifest = json.loads((bundle / "manifest.json").read_text())
    if manifest["architecture"] != platform.machine():
        raise ValueError("Bundle architecture differs from this computer")
    for name, expected in manifest["files"].items():
        target = (bundle / name).resolve()
        if (
            not target.is_relative_to(bundle)
            or hashlib.sha256(target.read_bytes()).hexdigest() != expected
        ):
            raise ValueError("Bundle checksum validation failed")
    loaders = list((bundle / "lib").glob("ld-linux-*.so.*"))
    if len(loaders) != 1:
        raise ValueError("Bundle needs exactly one architecture-matching ELF loader")
    # Refuse to replace an existing node identity, data, credentials or units.
    if directory.exists() and any(directory.iterdir()):
        raise ValueError(
            "Installation exists; preserve it and update the runtime separately"
        )
    unitdir = Path.home() / ".config/systemd/user"
    if any(
        (unitdir / name).exists()
        for name in ["rn2xx3-radio.service", "rn2xx3-ui.service"]
    ):
        raise ValueError("Existing service units must be preserved")
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o700)
    runtime = directory / "runtime"
    shutil.copytree(bundle, runtime)
    binary = runtime / "bin/meshtasticd"
    binary.chmod(0o755)
    for d in ["data/node", "config.d", "available.d"]:
        (directory / d).mkdir(parents=True, mode=0o700)
    mac = bytearray(secrets.token_bytes(6))
    mac[0] = (mac[0] & 0xFC) | 2
    if mac[2:] == b"\0\0\0\0":
        mac[-1] = 1
    text = (
        'Lora:\n  Module: auto\nGeneral:\n  MACAddress: "'
        + ":".join(f"{b:02x}" for b in mac)
        + '"\n  TCPPort: 4403\n  MaxNodes: 200\n  ConfigDirectory: '
        + str(directory / "config.d")
        + "/\n  AvailableDirectory: "
        + str(directory / "available.d")
        + "/\nHostMetrics:\n  ReportInterval: 0\nLogging:\n  LogLevel: warn\nWebserver:\n  Port: -1\n"
    )
    write_private(directory / "config.yaml", text)
    write_private(
        directory / "radio.env",
        "MESHTASTIC_RN2XX3_SERIAL="
        + str(device)
        + "\nMESHTASTIC_RN2XX3_TX=0\nMESHTASTIC_RN2XX3_LOOPBACK=1\n",
    )
    write_private(
        directory / "ui-login.json",
        json.dumps({"username": "meshadmin", "password": secrets.token_urlsafe(32)})
        + "\n",
    )
    venv = directory / "venv"
    subprocess.run(["python3", "-m", "venv", str(venv)], check=True)
    subprocess.run(
        [
            str(venv / "bin/pip"),
            "install",
            "--disable-pip-version-check",
            "meshtastic==2.7.11",
        ],
        check=True,
    )
    shutil.copyfile(
        Path(__file__).with_name("portable_ui.py"), directory / "portable_ui.py"
    )
    unitdir.mkdir(parents=True, exist_ok=True)
    loader = runtime / "lib" / loaders[0].name
    radio = """[Unit]
Description=RN2903 Meshtastic host radio
After=network.target
[Service]
Type=simple
WorkingDirectory=BASE
EnvironmentFile=BASE/radio.env
ExecStart=LOADER --library-path BASE/runtime/lib BASE/runtime/bin/meshtasticd --fsdir=BASE/data/node --config=BASE/config.yaml
Restart=on-failure
RestartSec=5
UMask=0077
NoNewPrivileges=true
MemoryMax=256M
[Install]
WantedBy=default.target
""".replace(
        "BASE", str(directory)
    ).replace(
        "LOADER", str(loader)
    )
    ui = """[Unit]
Description=LoStik local mesh UI
After=rn2xx3-radio.service
[Service]
Type=simple
WorkingDirectory=BASE
ExecStart=BASE/venv/bin/python BASE/portable_ui.py --credentials BASE/ui-login.json
Restart=on-failure
RestartSec=5
UMask=0077
NoNewPrivileges=true
MemoryMax=128M
[Install]
WantedBy=default.target
""".replace(
        "BASE", str(directory)
    )
    for name, content in [("rn2xx3-radio.service", radio), ("rn2xx3-ui.service", ui)]:
        write_private(unitdir / name, content)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(
        [
            "systemctl",
            "--user",
            "enable",
            "--now",
            "rn2xx3-radio.service",
            "rn2xx3-ui.service",
        ],
        check=True,
    )
    print(
        "User services installed. TX disabled. UI credentials: "
        + str(directory / "ui-login.json")
    )
    print(
        "For startup without login: loginctl enable-linger "
        + os.environ.get("USER", "YOUR_USER")
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument(
        "--directory", type=Path, default=Path.home() / ".local/share/rn2xx3"
    )
    parser.add_argument("--device", type=Path, required=True)
    args = parser.parse_args()
    try:
        install(args.bundle, args.directory, args.device)
    except (ValueError, subprocess.CalledProcessError) as e:
        raise SystemExit(str(e))


if __name__ == "__main__":
    main()
