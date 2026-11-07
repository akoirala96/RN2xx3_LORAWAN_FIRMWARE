#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Run inside the architecture-matching build container to export a Pi runtime."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--binary", type=Path, required=True)
p.add_argument("--output", type=Path, required=True)
args = p.parse_args()
os.umask(0o022)
if args.output.exists() and any(args.output.iterdir()):
    raise SystemExit("Output must be empty; never overwrite an installed node")
(args.output / "bin").mkdir(parents=True, exist_ok=True)
(args.output / "lib").mkdir()
binary = args.output / "bin/meshtasticd"
shutil.copyfile(args.binary, binary)
binary.chmod(0o755)
subprocess.run(["strip", "--strip-unneeded", str(binary)], check=True)
linked = subprocess.check_output(["ldd", str(binary)], text=True)
if "not found" in linked:
    raise SystemExit("Runtime dependency missing in builder")
for name in re.findall(r"(/[^\s()]+)", linked):
    src = Path(name)
    if src.is_file():
        dest = args.output / "lib" / src.name
        shutil.copyfile(src, dest)
        if src.name.startswith("ld-linux-"):
            dest.chmod(0o755)
notice = args.output / "licenses"
notice.mkdir()
for f in Path("/usr/share/doc").glob("*/copyright"):
    if f.is_file():
        shutil.copyfile(f, notice / (f.parent.name + ".copyright"))
source = Path(__file__).resolve().parents[1]
shutil.copyfile(source / "COPYING", notice / "RN2xx3-host-GPL-3.txt")
manifest = {
    "architecture": platform.machine(),
    "meshtastic_commit": "54e0d8d0ab2ff56b3a9ce967e53f79e49af560fb",
    "source": "https://github.com/akoirala96/RN2xx3_LORAWAN_FIRMWARE/tree/meshtastic-rn2903-host-driver/meshtastic",
    "files": {
        str(f.relative_to(args.output)): hashlib.sha256(f.read_bytes()).hexdigest()
        for f in sorted(args.output.rglob("*"))
        if f.is_file()
    },
}
(args.output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
print(
    "Native runtime exported for "
    + manifest["architecture"]
    + "; "
    + str(len(manifest["files"]))
    + " files with SHA-256 checksums"
)
