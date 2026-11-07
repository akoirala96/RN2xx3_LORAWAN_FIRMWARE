#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Apply the RN2903 adapter to one exact, reproducible Meshtastic revision."""
import argparse
from pathlib import Path
import shutil
import subprocess

COMMIT = "54e0d8d0ab2ff56b3a9ce967e53f79e49af560fb"
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("source", type=Path)
args = parser.parse_args()
source = args.source.resolve()
actual = subprocess.check_output(
    ["git", "rev-parse", "HEAD"], cwd=source, text=True
).strip()
if actual != COMMIT:
    raise SystemExit("Refusing an unreviewed Meshtastic revision: expected " + COMMIT)
driver = Path(__file__).resolve().parent.parent / "driver"
target = source / "src/platform/portduino"
for name in ["RN2xx3Serial.h", "RN2xx3Interface.h", "RN2xx3Interface.cpp"]:
    shutil.copyfile(driver / name, target / name)


def replace_once(name, old, new):
    path = source / name
    text = path.read_text()
    if new in text:
        return
    if text.count(old) != 1:
        raise SystemExit("Upstream source shape changed: " + name)
    path.write_text(text.replace(old, new, 1))


replace_once(
    "src/mesh/RadioInterface.cpp",
    '#include "platform/portduino/SimRadio.h"',
    '#include "platform/portduino/SimRadio.h"\n#include "platform/portduino/RN2xx3Interface.h"',
)
replace_once(
    "src/mesh/RadioInterface.cpp",
    "    std::unique_ptr<RadioInterface> rIf = nullptr;\n",
    """    std::unique_ptr<RadioInterface> rIf = nullptr;
#ifdef ARCH_PORTDUINO
    if (const char *device = std::getenv("MESHTASTIC_RN2XX3_SERIAL")) {
        if (!*device) { LOG_ERROR("Empty RN2903 UART path"); exit(EXIT_FAILURE); }
        rIf = std::make_unique<RN2xx3Interface>(device);
        if (!rIf->init()) exit(EXIT_FAILURE);
        radioType = RF95_RADIO;
        return rIf;
    }
#endif
""",
)
# Skip physical SPI/GPIO auto-probing, but use the real RN adapter above, not
# simulated radio traffic. The environment variable is an explicit opt-in.
replace_once(
    "src/platform/portduino/PortduinoGlue.cpp",
    "    if (portduino_config.lora_module == use_autoconf) {",
    """    if (std::getenv("MESHTASTIC_RN2XX3_SERIAL")) {
        portduino_config.lora_module = use_simradio;
    }
    if (portduino_config.lora_module == use_autoconf) {""",
)
replace_once(
    "src/main.cpp",
    '    if (portduino_config.lora_spi_dev != "ch341") {',
    '    if (!std::getenv("MESHTASTIC_RN2XX3_SERIAL") && portduino_config.lora_spi_dev != "ch341") {',
)
replace_once(
    "bin/platformio-custom.py",
    "current_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)\nbuild_epoch = int(current_date.timestamp())",
    "# Fixed host-adapter build epoch (2026-11-06 UTC) for repeatable builds.\nbuild_epoch = 1793923200",
)
replace_once(
    "src/platform/portduino/PortduinoGlue.cpp",
    '    if (portduino_config.lora_spi_dev != "" && portduino_config.lora_spi_dev != "ch341") {',
    '    if (!std::getenv("MESHTASTIC_RN2XX3_SERIAL") && portduino_config.lora_spi_dev != "" && portduino_config.lora_spi_dev != "ch341") {',
)
# PlatformIO installs its pinned framework before running a project pre-script.
# This optional loopback binding is needed by a native, non-Docker Pi install.
shutil.copyfile(Path(__file__).with_name("pio_pre.py"), source / "rn2xx3-pio-pre.py")
replace_once(
    "platformio.ini",
    "extra_scripts =\n\tpre:bin/platformio-pre.py",
    "extra_scripts =\n\tpre:rn2xx3-pio-pre.py\n\tpre:bin/platformio-pre.py",
)
print("RN2903 adapter applied to pinned Meshtastic " + COMMIT)
