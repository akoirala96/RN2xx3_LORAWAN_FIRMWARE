# SPDX-License-Identifier: GPL-3.0-or-later
"""Optional loopback API binding for native installs; framework remains LGPL."""
from pathlib import Path

Import("env")
framework = Path(env.PioPlatform().get_package_dir("framework-portduino"))
path = framework / "libraries/WiFi/src/WiFiServer.cpp"
old = "  serv_addr.sin_addr.s_addr = INADDR_ANY;"
new = """  // RN2903 rootless host installs keep the unauthenticated API private.
  serv_addr.sin_addr.s_addr = getenv("MESHTASTIC_RN2XX3_LOOPBACK")
      ? htonl(INADDR_LOOPBACK) : INADDR_ANY;"""
text = path.read_text()
if new not in text:
    if text.count(old) != 1:
        raise RuntimeError("Unreviewed Portduino API binding implementation")
    path.write_text(text.replace(old, new, 1))
