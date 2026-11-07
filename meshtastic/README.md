# Experimental Meshtastic host adapter for RN2903 LoStik

Run a Meshtastic node using an existing Ronoth LoStik RN2903 and a Linux computer or Raspberry Pi. The computer runs the real Meshtastic routing, encryption, node database and client API. A UART radio driver controls the LoStik's stock raw-LoRa commands.

**This is a host adapter, not a Meshtastic firmware image for the PIC inside the LoStik.** Keep the Linux host powered and connected. No flashing, soldering, LoRaWAN firmware rebuild or EEPROM writes are needed. It is an experimental community implementation; independent stock Meshtastic hardware interoperability is not yet verified. See [hardware test results](TEST-RESULTS.md).

## Supported setup

- RN2903 reporting `RN2903 1.0.5 Nov 06 2018 10:45:27`, including `radio rxstop` and `radio get reg 18`.
- Linux x86-64 and Raspberry Pi running a 64-bit ARM Linux OS. The portable installation targets Debian 13 / Python 3.13; a Pi 3 B is the small-host test target.
- US region only, within 902–928 MHz. RN2483 and other regions are not implemented.
- LoRa bandwidth 125/250/500 kHz, SF7–SF12 and coding rate 4/5–4/8. The default provisioner chooses US LongFast, 906.875 MHz, SF11, BW250, CR4/5, preamble 16 and sync word `0x2b`.
- Radio output is limited to 2–17 dBm. Initial tests use 2 dBm.

The source pins Meshtastic to [`54e0d8d0ab2ff56b3a9ce967e53f79e49af560fb`](https://github.com/meshtastic/firmware/tree/54e0d8d0ab2ff56b3a9ce967e53f79e49af560fb), including its submodules, framework and radio library versions. PlatformIO is 6.1.19; the Python client is 2.7.11. The overlay refuses another upstream revision and fixes the build timestamp at `1793923200` (2026-11-06 UTC), avoiding an upstream daily timestamp that otherwise invalidates the build cache. Container base images and the optional MeshMonitor UI image use immutable digests. Debian package repositories and Python transitive dependencies remain updateable, so these are repeatable builds, not a promise of byte-identical binaries.

## Docker installation

Run these commands as your ordinary Docker user. Select the actual radio path from `/dev/serial/by-id/`; the CH340 identifier below matches the tested LoStiks. Avoid attaching two identical CH340 radios to the same host without configuring distinct USB paths.

```bash
cd meshtastic
python3 tools/setup.py --device /dev/serial/by-id/usb-1a86_USB2.0-Serial-if00-port0
docker compose build
docker compose up -d node
```

The setup tool generates a persistent local MAC, private data directories and UI credentials in `.env`. It preserves an existing complete setup and refuses a partial one. **Transmission starts disabled** through `RADIO_TX_ENABLED=0`. The client API is published on `127.0.0.1:4403` only. The built-in native HTTP server is disabled.

Provision a matching US channel while TX remains gated off:

```bash
docker compose exec -T node /opt/meshtastic-client/bin/python /opt/rn2xx3/tools/configure_node.py --region US --name "LoStik Home" --short-name HOME --power 2
```

Attach the correct antennas before transmitting. Edit `.env` to set `RADIO_TX_ENABLED=1`, then apply it:

```bash
docker compose up -d node
```

The driver follows the native Meshtastic `lora.tx_enabled` flag as well as its separate hardware TX gate. Initial provisioning uses the standard public LongFast channel; its shared default key does not provide private group messaging. Configure a matching private channel through Meshtastic if that is needed. MQTT is not required for radio operation.

For a full browser map, messages, channels and administration, start the optional MeshMonitor UI:

```bash
docker compose --profile ui up -d
```

Open `http://localhost:8080`. The generated `UI_ADMIN_USERNAME` and `UI_ADMIN_PASSWORD` are in your local `.env`; anonymous access is disabled. An SSH forward to the host also works. Stop the UI while an independent CLI tool uses the radio API if the clients displace one another. Keep the raw client API private; it does not authenticate clients.

For public HTTPS access, place the UI behind an authenticated reverse proxy and configure its native OIDC login. Do not expose port 4403. In deployments using MeshMonitor behind another login gateway, disable session rolling and prevent static asset responses from setting cookies; late asset responses can otherwise restore a pre-login cookie during OIDC authentication.

## Portable Raspberry Pi without Docker or root

Build on a larger machine; a Pi 3 B has little RAM for a full compiler toolchain. Use an ARM64 build host, or a Docker builder with ARM64 emulation enabled. Build the **same architecture as the Pi's OS**, even though its CPU can support multiple architectures.

On the build machine:

```bash
cd meshtastic
docker buildx build --platform linux/arm64 --target build --build-arg BUILD_JOBS=2 --load --tag rn2xx3-arm64-builder .
mkdir native-bundle
docker run --rm --platform linux/arm64 --entrypoint python3 -v "$PWD/native-bundle:/export" rn2xx3-arm64-builder /adapter/tools/export_native.py --binary /work/firmware/.pio/build/native/meshtasticd --output /export
```

The bundle contains the native binary, required shared libraries, ELF loader, license notices and SHA-256 manifest. Transfer `native-bundle/` and `tools/` to a private installation folder on the Pi. The installer requires Python venv support, systemd user services and read/write access to the radio (normally membership in `dialout`). It checks bundle architecture and checksums and refuses an existing installation or service units.

On the Pi:

```bash
python3 tools/install_native.py --bundle native-bundle --device /dev/serial/by-id/usb-1a86_USB2.0-Serial-if00-port0
loginctl enable-linger "$USER"
systemctl --user stop rn2xx3-ui
~/.local/share/rn2xx3/venv/bin/python tools/configure_node.py --region US --name "LoStik Walking" --short-name WALK --power 2
```

Attach the antenna. In `~/.local/share/rn2xx3/radio.env`, change `MESHTASTIC_RN2XX3_TX=0` to `MESHTASTIC_RN2XX3_TX=1`. Then:

```bash
systemctl --user restart rn2xx3-radio
systemctl --user start rn2xx3-ui
```

Both services start after reboot once lingering is enabled. This installation uses the Pi's existing network configuration. Radio operation continues without Internet access. To open the lightweight UI from a computer that can reach the Pi, use this forward, replacing the SSH destination:

```bash
ssh -N -L 8080:127.0.0.1:8080 YOUR_USER@PI_ADDRESS
```

Open `http://localhost:8080`. Login details are saved privately in `~/.local/share/rn2xx3/ui-login.json`. The UI shows heard nodes, recent text messages and reported positions, and sends messages on enabled channels. Its relative position plot works without Internet map tiles. It requires login and validates the Host, Origin and CSRF token for sends. The Pi radio API and UI bind only to loopback.

The LoStik has no GPS receiver. Moving the Pi does not automatically produce a walking GPS track; attach/configure a location source to report positions. Keep backups of the private installation directory, including the node data and MAC configuration, to preserve identity. Avoid running copied identities on two nodes simultaneously.

## Experimental Raspberry Pi Bluetooth transport

The optional BlueZ peripheral presents the official Meshtastic BLE service and forwards protobuf messages to the same native node. It requires a working Bluetooth adapter, BlueZ's GATT and advertising D-Bus APIs, and permission for the regular user to register a peripheral and pairing agent. Pairing, configuration transfer, phone-originated radio messaging and remote radio administration were tested with a Samsung Galaxy S26 Ultra and a Pi 3 B. Other phone platforms remain unverified. Bluetooth runs on the Pi; it does not flash the LoStik or provide Bluetooth through its USB chip.

On the Pi, after installing the native node:

```bash
python3 tools/install_ble.py --name WALK_PI
```

If Bluetooth is software-blocked, an administrator can unblock it with `sudo rfkill unblock bluetooth`. For a dedicated Pi whose dual-mode controller causes Android bonding problems, `sudo python3 tools/enable_ble_controller.py --apply` saves a backup, sets `ControllerMode=le` and `Privacy=device`, and powers the controller off before BlueZ starts. BlueZ then manages the private addresses itself. This affects other Bluetooth uses of that Pi; preview the script without `--apply` first. It is not needed on every system. An external manual static-address override can make the advertised address disagree with BlueZ's GATT listener; the tested setup uses BlueZ-managed privacy.

Start the local pairing helper **before** selecting `WALK_PI` on the phone:

```bash
systemctl --user restart rn2xx3-ble
python3 ~/.local/share/rn2xx3/pair_ble.py
```

The helper displays a generated PIN locally when Android requests PIN entry. When Android requests numeric comparison instead, type the six digits shown on the phone into the Pi terminal and confirm Pair on Android. Numbers must match the current request within its timeout; the bridge never automatically approves numeric comparison or unauthenticated pairing. Keep pairing files and codes private. The helper stays open across cancelled attempts for up to fifteen minutes.

The successful Android setup paired `WALK_PI` in system Bluetooth settings first, then selected it in Meshtastic. If the app repeatedly rejects a cached entry, use that sequence. After changing controller identity or forgetting a device, stale keys may remain on the other side: forget that radio on Android and remove only the corresponding phone bond on the Pi with `bluetoothctl remove PHONE_ADDRESS`, then reopen the pairing window. Do not erase unrelated Bluetooth bonds.

New pairing is available for three minutes after a service restart. Established bonds remain usable afterward. The native radio API stays on loopback, and the Pi browser UI yields its API connection while a paired phone uses BLE. Bluetooth settings in Meshtastic's native radio configuration do not configure this separate BlueZ bridge; manage it with `systemctl --user start/stop rn2xx3-ble`.

## Joining the nearby mesh

Use US LongFast with the standard primary channel to participate in the public mesh in the supported US region. Normal `CLIENT` role and `ALL` rebroadcast mode forward eligible traffic without assigning an infrastructure-router role to this experimental adapter. Both tested nodes used those settings with a hop limit of three. MQTT is not needed.

Nodes appear when compatible radio traffic is heard directly or learned through relays; the interface is not an inventory of every nearby LoRa device. Antenna placement, range, matching modem settings and local mesh activity determine which nodes are heard. Independent stock-radio interoperability still needs a field test.

For remote administration, add the controlling node's **public** key to an Admin Key slot in the target radio's Security settings, then open that target's Remote Administration page while connected to the controlling node. The tested phone-to-Pi-to-stationary-node path loaded remote configuration successfully. Keep node private keys private. A stationary node without GPS can use a fixed position configured through the normal Meshtastic administration API; this is a stored map position, not live GPS.

## Tests and radio verification

No radio transmissions are made by the automated test suite:

```bash
python3 -m unittest discover -s tests -v
CXXFLAGS="-fsanitize=address,undefined -fno-omit-frame-pointer -fno-pie -no-pie" python3 -m unittest discover -s tests -p test_uart.py -v
```

The UART tests use a real C++ serial transport against pseudo-terminals. They cover fragmented replies, the stock firmware's double-space receive formatter, RF events arriving during commands, bounded timeouts and queues, oversized input, exclusive ownership and command injection. UI tests cover authentication, cross-site requests, CSRF, host validation and request size limits. Install `dbus-next==0.2.3` to include BLE contract tests for service UUIDs, authenticated permissions, private packet data and explicit pairing confirmation. CI runs these without radio hardware; full native builds can be requested with the workflow dispatcher.

A read-only capability probe is also available:

```bash
g++ -std=c++17 -O2 tools/probe.cpp -o rn2xx3-probe
./rn2xx3-probe /dev/serial/by-id/usb-1a86_USB2.0-Serial-if00-port0
```

Stop the radio service before probing it. `--rx-us-longfast` additionally applies a temporary profile and listens for 45 seconds, restoring the recorded settings afterward. It does not transmit. Do not run raw-LoRa tests while using the same module for a LoRaWAN session.

For an actual two-radio test, provide two distinct local API ports (use an SSH forward for the second host), stop other API clients, attach both antennas and explicitly opt in:

```bash
python3 tools/ota_test.py --a-port 4403 --b-port 4404 --antennas-attached --send
```

The test sends standard node information in both directions, then a directed text message in each direction. It requires the peer to receive/decrypt each message and the sender to receive its native routing acknowledgment. A `radio_tx_ok` modem response alone is not treated as delivery. Results distinguish the two-adapter test from an independent stock-radio interoperability test. Other nearby nodes will appear only if compatible nodes are heard on the selected radio profile and channel; silence is not proof that no nodes exist.

## Boundaries and recovery

The UART cannot provide true Semtech hardware CAD through the supported stock command API. The driver checks modem receive activity before transmitting and uses Meshtastic's native randomized scheduling, but this is not equivalent to a fully supported SPI radio driver. Host scheduling, USB timing, collisions, maximum-payload reception, long-term reliability and real stock-radio interoperability need further field testing.

The daemon owns the UART exclusively. A supervisor restart stops any reception left running by the previous process before applying and verifying the profile. USB errors cause the daemon to exit for supervised recovery. Volatile settings are restored on destruction when possible; no `mac save` or firmware update is issued. A power cycle restores the modem's saved configuration. Changing Linux radio or UI code does not change the PIC firmware.

## Licensing

Files added under `meshtastic/` are GPL-3.0-or-later; see [COPYING](COPYING). Meshtastic and its dependencies retain their own licenses. The optional Portduino framework patch retains that framework's LGPL license. Native exports include Debian dependency copyright notices. The original Microchip source elsewhere in this repository retains the root [LICENSE](../LICENSE); none of its firmware source or objects is linked into the host adapter.
