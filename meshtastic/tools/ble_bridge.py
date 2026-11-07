#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Authenticated BlueZ Meshtastic peripheral, forwarding the real native API."""
import argparse
import asyncio
import json
import os
import secrets
import hmac
from pathlib import Path
import signal
import tempfile
import time

from dbus_next import BusType, DBusError, Message, MessageType, PropertyAccess, Variant
from dbus_next.aio import MessageBus
from dbus_next.service import ServiceInterface, dbus_property, method
from ble_transport import Decoder, Mailbox, encode

SERVICE = "6ba1b218-15a8-461f-9fa8-5dcae273eafd"
UUIDS = {
    "fromradio": "2c55e69e-4993-11ed-b878-0242ac120002",
    "toradio": "f75c76d2-129e-4dad-a1dd-7866124401e7",
    "fromnum": "ed9da18c-a800-4f66-a670-aa7547e34453",
}
APP = "/org/eorange/meshtastic"
GATT = "org.bluez.GattCharacteristic1"
SVC = "org.bluez.GattService1"
PROPS = "org.freedesktop.DBus.Properties"
OM = "org.freedesktop.DBus.ObjectManager"
FLAGS = {
    "fromradio": ["read", "encrypt-authenticated-read"],
    "toradio": ["write", "encrypt-authenticated-write"],
    "fromnum": ["read", "notify", "encrypt-authenticated-read"],
}


def value(options, name, default=None):
    result = options.get(name)
    return result.value if result is not None else default


class Service(ServiceInterface):
    def __init__(self):
        super().__init__(SVC)

    @dbus_property(access=PropertyAccess.READ)
    def UUID(self) -> "s":
        return SERVICE

    @dbus_property(access=PropertyAccess.READ)
    def Primary(self) -> "b":
        return True

    @dbus_property(access=PropertyAccess.READ)
    def Includes(self) -> "ao":
        return []


class Characteristic(ServiceInterface):
    def __init__(self, bridge, kind):
        self.bridge = bridge
        self.kind = kind
        self.notifying = False
        super().__init__(GATT)

    @dbus_property(access=PropertyAccess.READ)
    def UUID(self) -> "s":
        return UUIDS[self.kind]

    @dbus_property(access=PropertyAccess.READ)
    def Service(self) -> "o":
        return APP + "/service0"

    @dbus_property(access=PropertyAccess.READ)
    def Flags(self) -> "as":
        return FLAGS[self.kind]

    @dbus_property(access=PropertyAccess.READ)
    def Notifying(self) -> "b":
        return self.notifying

    @dbus_property(access=PropertyAccess.READ)
    def Value(self) -> "ay":
        # Never expose FromRadio contents as a system D-Bus property.
        return (
            self.bridge.mailbox.number.to_bytes(4, "little")
            if self.kind == "fromnum"
            else b""
        )

    @method()
    async def ReadValue(self, options: "a{sv}") -> "ay":
        if self.kind == "toradio":
            raise DBusError("org.bluez.Error.NotPermitted", "Write characteristic")
        await self.bridge.ensure_session(value(options, "device"))
        offset = value(options, "offset", 0)
        try:
            if self.kind == "fromradio":
                return self.bridge.mailbox.read(offset)
            data = self.Value
            if not 0 <= offset <= len(data):
                raise ValueError()
            return data[offset:]
        except ValueError:
            raise DBusError("org.bluez.Error.InvalidOffset", "Invalid read offset")

    @method()
    async def WriteValue(self, data: "ay", options: "a{sv}"):
        if self.kind != "toradio":
            raise DBusError("org.bluez.Error.NotPermitted", "Read characteristic")
        if value(options, "offset", 0) != 0 or value(
            options, "prepare-authorize", False
        ):
            raise DBusError(
                "org.bluez.Error.NotSupported", "Prepared partial writes unsupported"
            )
        try:
            frame = encode(bytes(data))
        except ValueError:
            raise DBusError(
                "org.bluez.Error.InvalidValueLength", "Invalid protobuf length"
            )
        await self.bridge.ensure_session(value(options, "device"))
        if not self.bridge.writer:
            raise DBusError("org.bluez.Error.Failed", "Native radio unavailable")
        self.bridge.writer.write(frame)
        try:
            await asyncio.wait_for(self.bridge.writer.drain(), 5)
        except Exception:
            raise DBusError("org.bluez.Error.Failed", "Native radio unavailable")

    @method()
    def StartNotify(self):
        if self.kind != "fromnum":
            raise DBusError(
                "org.bluez.Error.NotSupported", "Notification characteristic required"
            )
        self.notifying = True
        self.emit_properties_changed({"Notifying": True, "Value": self.Value})

    @method()
    def StopNotify(self):
        self.notifying = False
        self.emit_properties_changed({"Notifying": False})

    def notify(self):
        if self.notifying:
            self.emit_properties_changed({"Value": self.Value})


class Application(ServiceInterface):
    def __init__(self, bridge):
        self.bridge = bridge
        super().__init__(OM)

    @method()
    def GetManagedObjects(self) -> "a{oa{sa{sv}}}":
        objects = {
            APP
            + "/service0": {
                SVC: {
                    "UUID": Variant("s", SERVICE),
                    "Primary": Variant("b", True),
                    "Includes": Variant("ao", []),
                }
            }
        }
        for kind, char in self.bridge.chars.items():
            objects[APP + "/service0/" + kind] = {
                GATT: {
                    "UUID": Variant("s", UUIDS[kind]),
                    "Service": Variant("o", APP + "/service0"),
                    "Flags": Variant("as", FLAGS[kind]),
                    "Notifying": Variant("b", char.notifying),
                    "Value": Variant("ay", char.Value),
                }
            }
        return objects


class Advertisement(ServiceInterface):
    def __init__(self, name):
        self.local_name = name
        super().__init__("org.bluez.LEAdvertisement1")

    @dbus_property(access=PropertyAccess.READ)
    def Type(self) -> "s":
        return "peripheral"

    @dbus_property(access=PropertyAccess.READ)
    def ServiceUUIDs(self) -> "as":
        return [SERVICE]

    @dbus_property(access=PropertyAccess.READ)
    def LocalName(self) -> "s":
        return self.local_name

    @dbus_property(access=PropertyAccess.READ)
    def Discoverable(self) -> "b":
        # Explicit LE flags let centrals discard a cached dual-mode identity.
        return True

    @method()
    def Release(self):
        pass


class Agent(ServiceInterface):
    def __init__(self, bridge):
        self.bridge = bridge
        super().__init__("org.bluez.Agent1")

    @method()
    def Release(self):
        pass

    @method()
    def DisplayPasskey(self, device: "o", passkey: "u", entered: "q"):
        self.bridge.pairing_state("enter_this_pin_on_phone", f"{passkey:06d}")

    @method()
    def DisplayPinCode(self, device: "o", pincode: "s"):
        raise DBusError("org.bluez.Error.Rejected", "BLE passkey pairing required")

    @method()
    def RequestPinCode(self, device: "o") -> "s":
        raise DBusError("org.bluez.Error.Rejected", "BLE passkey pairing required")

    @method()
    def RequestPasskey(self, device: "o") -> "u":
        raise DBusError(
            "org.bluez.Error.Rejected", "Use the PIN displayed by this peripheral"
        )

    @method()
    async def RequestConfirmation(self, device: "o", passkey: "u"):
        bridge = self.bridge
        if bridge.confirmation_token:
            raise DBusError(
                "org.bluez.Error.Rejected", "Another pairing confirmation is pending"
            )
        token = secrets.token_urlsafe(32)
        bridge.confirmation_token = token
        approval = bridge.args.pairing.with_suffix(".approved.json")
        approval.unlink(missing_ok=True)
        code = f"{passkey:06d}"
        bridge.pairing_state("confirm_number_matches_phone", code, token)
        deadline = time.monotonic() + getattr(bridge.args, "confirmation_timeout", 25)
        try:
            while time.monotonic() < deadline and bridge.confirmation_token == token:
                try:
                    state = json.loads(approval.read_text())
                    if (
                        hmac.compare_digest(str(state.get("token", "")), token)
                        and state.get("code") == code
                    ):
                        bridge.pairing_state("number_confirmed")
                        return
                except (FileNotFoundError, ValueError):
                    pass
                await asyncio.sleep(0.1)
            raise DBusError(
                "org.bluez.Error.Rejected", "Pairing number was not confirmed locally"
            )
        finally:
            if bridge.confirmation_token == token:
                bridge.confirmation_token = None
            approval.unlink(missing_ok=True)

    @method()
    def RequestAuthorization(self, device: "o"):
        self.bridge.pairing_state("unauthenticated_pairing_requested")
        raise DBusError(
            "org.bluez.Error.Rejected", "Unauthenticated pairing is disabled"
        )

    @method()
    def AuthorizeService(self, device: "o", uuid: "s"):
        if uuid.lower() != SERVICE:
            raise DBusError("org.bluez.Error.Rejected", "Meshtastic service only")

    @method()
    def Cancel(self):
        self.bridge.confirmation_token = None
        self.bridge.pairing_state("pairing_cancelled")


class Bridge:
    def __init__(self, args):
        self.args = args
        self.bus = None
        self.devices = {}
        self.owner = None
        self.writer = None
        self.pump = None
        self.session_lock = asyncio.Lock()
        self.mailbox = Mailbox()
        self.chars = {k: Characteristic(self, k) for k in UUIDS}
        self.stop = asyncio.Event()
        self.confirmation_token = None

    def pairing_state(self, status, passkey=None, token=None):
        data = {
            "name": self.args.name,
            "status": status,
            "passkey": passkey,
            "updated_at": int(time.time()),
        }
        if token:
            data["token"] = token
        fd, name = tempfile.mkstemp(
            prefix=".ble-pairing-", dir=self.args.pairing.parent
        )
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f)
                f.write("\n")
            os.replace(name, self.args.pairing)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def message(self, message):
        if message.message_type != MessageType.SIGNAL:
            return
        if (
            message.interface == PROPS
            and message.member == "PropertiesChanged"
            and message.body[0] == "org.bluez.Device1"
        ):
            props = self.devices.setdefault(message.path, {})
            props.update(message.body[1])
            if value(props, "Paired", False):
                self.pairing_state("paired")
            if message.path == self.owner and not value(props, "Connected", False):
                asyncio.create_task(self.release(False))
        if message.interface == OM and message.member == "InterfacesAdded":
            path, objects = message.body
            if "org.bluez.Device1" in objects:
                self.devices[path] = objects["org.bluez.Device1"]
        if (
            message.interface == "org.freedesktop.DBus"
            and message.member == "NameOwnerChanged"
            and message.body[0] == "org.bluez"
        ):
            self.stop.set()

    async def device_properties(self, path):
        if not isinstance(path, str) or not path.startswith("/org/bluez/"):
            raise DBusError("org.bluez.Error.NotAuthorized", "Bluetooth peer required")
        intro = await self.bus.introspect("org.bluez", path)
        proxy = self.bus.get_proxy_object("org.bluez", path, intro)
        props = await proxy.get_interface(PROPS).call_get_all("org.bluez.Device1")
        self.devices[path] = props
        return props, proxy

    async def ensure_session(self, path):
        async with self.session_lock:
            props, _ = await self.device_properties(path)
            if not value(props, "Paired", False) or not value(
                props, "Connected", False
            ):
                raise DBusError(
                    "org.bluez.Error.NotAuthorized",
                    "Authenticated Bluetooth pairing required",
                )
            if self.owner and self.owner != path:
                raise DBusError(
                    "org.bluez.Error.InProgress", "Another phone controls the radio"
                )
            if self.writer and self.owner == path:
                return
            self.owner = path
            self.args.lease.touch(mode=0o600)
            # Let the local browser client yield the single native API connection.
            await asyncio.sleep(1.25)
            try:
                reader, self.writer = await asyncio.wait_for(
                    asyncio.open_connection("127.0.0.1", self.args.port), 5
                )
                self.mailbox.clear()
                self.pump = asyncio.create_task(self.read_native(reader))
            except Exception:
                self.owner = None
                self.args.lease.unlink(missing_ok=True)
                raise DBusError("org.bluez.Error.Failed", "Native radio unavailable")

    async def read_native(self, reader):
        decoder = Decoder()
        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    break
                for frame in decoder.feed(data):
                    self.mailbox.push(frame)
                    self.chars["fromnum"].notify()
        except asyncio.CancelledError:
            return
        except Exception:
            pass
        await self.release(True)

    async def release(self, disconnect):
        path, self.owner = self.owner, None
        writer, self.writer = self.writer, None
        task, self.pump = self.pump, None
        if task and task is not asyncio.current_task():
            task.cancel()
        if writer:
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), 2)
            except Exception:
                pass
        self.mailbox.clear()
        self.args.lease.unlink(missing_ok=True)
        if disconnect and path:
            try:
                _, proxy = await self.device_properties(path)
                await proxy.get_interface("org.bluez.Device1").call_disconnect()
            except Exception:
                pass

    async def run(self):
        self.bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        self.bus.add_message_handler(self.message)
        intro = await self.bus.introspect("org.bluez", "/")
        root = self.bus.get_proxy_object("org.bluez", "/", intro)
        objects = await root.get_interface(OM).call_get_managed_objects()
        adapters = [
            p
            for p, i in objects.items()
            if "org.bluez.GattManager1" in i and "org.bluez.LEAdvertisingManager1" in i
        ]
        if not adapters:
            raise RuntimeError("No Bluetooth peripheral adapter")
        for path, interfaces in objects.items():
            if "org.bluez.Device1" in interfaces:
                self.devices[path] = interfaces["org.bluez.Device1"]
        for rule in [
            "type='signal',sender='org.bluez',interface='org.freedesktop.DBus.Properties'",
            "type='signal',sender='org.bluez',interface='org.freedesktop.DBus.ObjectManager'",
            "type='signal',interface='org.freedesktop.DBus',member='NameOwnerChanged',arg0='org.bluez'",
        ]:
            response = await self.bus.call(
                Message(
                    destination="org.freedesktop.DBus",
                    path="/org/freedesktop/DBus",
                    interface="org.freedesktop.DBus",
                    member="AddMatch",
                    signature="s",
                    body=[rule],
                )
            )
            if response.message_type == MessageType.ERROR:
                raise RuntimeError("Cannot subscribe to Bluetooth lifecycle")
        path = adapters[0]
        intro = await self.bus.introspect("org.bluez", path)
        adapter = self.bus.get_proxy_object("org.bluez", path, intro)
        props = adapter.get_interface(PROPS)
        await props.call_set("org.bluez.Adapter1", "Powered", Variant("b", True))
        await props.call_set(
            "org.bluez.Adapter1", "Alias", Variant("s", self.args.name)
        )
        app = Application(self)
        self.bus.export(APP, app)
        self.bus.export(APP + "/service0", Service())
        for kind, char in self.chars.items():
            self.bus.export(APP + "/service0/" + kind, char)
        advertisement = Advertisement(self.args.name)
        self.bus.export(APP + "/advertisement0", advertisement)
        agent = Agent(self)
        self.bus.export(APP + "/agent0", agent)
        intro = await self.bus.introspect("org.bluez", "/org/bluez")
        manager = self.bus.get_proxy_object(
            "org.bluez", "/org/bluez", intro
        ).get_interface("org.bluez.AgentManager1")
        await manager.call_register_agent(APP + "/agent0", "DisplayYesNo")
        await manager.call_request_default_agent(APP + "/agent0")
        gatt = adapter.get_interface("org.bluez.GattManager1")
        advertising = adapter.get_interface("org.bluez.LEAdvertisingManager1")
        registered_app = registered_adv = False
        try:
            await gatt.call_register_application(APP, {})
            registered_app = True
            await props.call_set(
                "org.bluez.Adapter1",
                "PairableTimeout",
                Variant("u", self.args.pairing_window),
            )
            await props.call_set("org.bluez.Adapter1", "Pairable", Variant("b", True))
            await props.call_set(
                "org.bluez.Adapter1",
                "DiscoverableTimeout",
                Variant("u", self.args.pairing_window),
            )
            await props.call_set(
                "org.bluez.Adapter1", "Discoverable", Variant("b", True)
            )
            await advertising.call_register_advertisement(APP + "/advertisement0", {})
            registered_adv = True
            self.pairing_state("ready_for_phone_pairing")
            print(
                "Meshtastic Bluetooth peripheral ready: " + self.args.name, flush=True
            )
            while not self.stop.is_set():
                if self.owner:
                    self.args.lease.touch(mode=0o600)
                try:
                    await asyncio.wait_for(self.stop.wait(), 3)
                except asyncio.TimeoutError:
                    pass
        finally:
            await self.release(False)
            if registered_adv:
                try:
                    await advertising.call_unregister_advertisement(
                        APP + "/advertisement0"
                    )
                except Exception:
                    pass
            if registered_app:
                try:
                    await gatt.call_unregister_application(APP)
                except Exception:
                    pass
            try:
                await manager.call_unregister_agent(APP + "/agent0")
            except Exception:
                pass
            self.bus.disconnect()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairing", type=Path, required=True)
    parser.add_argument("--lease", type=Path, required=True)
    parser.add_argument("--name", default="LoStik")
    parser.add_argument("--port", type=int, default=4403)
    parser.add_argument("--pairing-window", type=int, default=180)
    args = parser.parse_args()
    if not 1 <= len(args.name.encode()) <= 7:
        parser.error("Use a short BLE advertising name (1–7 bytes)")
    if args.pairing.parent.stat().st_mode & 0o077:
        parser.error("Pairing directory must be private (0700)")
    os.umask(0o077)
    args.lease.unlink(missing_ok=True)
    bridge = Bridge(args)

    async def run():
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, bridge.stop.set)
        await bridge.run()

    try:
        asyncio.run(run())
    except Exception as error:
        # D-Bus names are safe; never log PINs, device addresses or radio data.
        print(
            "Bluetooth bridge initialization failed: "
            + getattr(error, "type", type(error).__name__),
            flush=True,
        )
        raise SystemExit(1)
    finally:
        args.lease.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
