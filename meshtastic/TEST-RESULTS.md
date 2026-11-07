# Hardware and software validation

Status: experimental. Two real LoStiks passed native Meshtastic message delivery in both directions on 2026-11-07 UTC.

| Check | Observed result |
| --- | --- |
| Hardware | Two USB RN2903 LoStiks reporting 1.0.5, dated 2018-11-06 |
| Hosts | Linux x86-64 stationary node and Raspberry Pi 3 B with a 64-bit ARM OS |
| Profile | US LongFast: 906.875 MHz, SF11, BW250, CR4/5, preamble 16, sync `0x2b`, initial output 2 dBm; modem settings read back |
| Node information | Standard native node-information packets received in both directions |
| Directed text, A to B | Peer received and decoded the text; sender received a successful native routing acknowledgment; RSSI −64 dBm, SNR 6 dB |
| Directed text, B to A | Peer received and decoded the text; sender received a successful native routing acknowledgment; RSSI −65 dBm, SNR 7 dB |
| Path | Actual LoRa radio delivery; received packets were not marked as MQTT traffic |
| Home UI | Real browser login, ordinary-user separation and configured stationary radio source passed |
| Pi deployment | Native ARM64 binary and user services run on the Pi; API and browser UI bind to loopback |
| Bluetooth | Samsung Galaxy S26 Ultra paired with the Pi; authenticated, bonded and connected link, completed service discovery, native configuration transfer and notifications verified |
| Phone-originated text | Received over LoRa at the stationary node and displayed in MeshMonitor; RSSI −52 dBm, SNR 6 dB, not MQTT |
| Text back to the phone's node | Stationary sender received a successful acknowledgment whose source was the walking node |
| Remote administration | Walking node's public key authorized on the stationary node; phone user confirmed remote device configuration loaded |

These tests use the real upstream Meshtastic native encryption, routing and client API. A modem's `radio_tx_ok` response alone is not counted as delivery. Neither host uses a simulated radio for these tests.

Hardware tests caught and corrected three significant issues: the stock modem emits `radio_rx` with two separator spaces; its idle RX-on status bit does not mean a packet is currently arriving; and continuous reception survives a host-process restart, requiring an explicit RX stop during initialization.

Bluetooth testing also caught a D-Bus advertisement-name collision, an overly restrictive numeric-confirmation handler, Android cached dual-mode identity behavior and stale bond keys. The final Pi configuration uses LE-only mode, BlueZ-managed device privacy, explicit LE advertising flags and authenticated GATT permissions. A manual static-address experiment was removed before successful service discovery. Only the dedicated phone bond was reset during recovery.

The host suite passed 29 UART/UI/BLE tests, and the nine UART tests also passed AddressSanitizer and UndefinedBehaviorSanitizer. A later BLE advertisement contract check passed all eight GATT tests. No automated host test transmits radio traffic.

An initial 45-second receive-only sample heard no packets and produced no modem errors. That sample establishes neither the presence nor absence of public neighbors.

No independent stock Meshtastic MCU radio has been tested. General stock-radio interoperability, range, long-term reliability, collisions, maximum payloads, power consumption and walking GPS operation remain unverified. This UART interface cannot provide true Semtech hardware CAD. The LoStik has no built-in GPS.
