# SPDX-License-Identifier: GPL-3.0-or-later
import asyncio
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
if importlib.util.find_spec("dbus_next") is None:
    raise unittest.SkipTest("Install dbus-next==0.2.3 to run BlueZ contract tests")
from dbus_next import DBusError, Variant
from dbus_next.service import ServiceInterface
from ble_bridge import Advertisement, Agent, Application, Bridge, FLAGS, SERVICE


class GattTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.bridge = Bridge(
            SimpleNamespace(
                name="LoStik",
                lease=self.base / "lease",
                pairing=self.base / "pairing.json",
                port=4403,
            )
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_advertised_name_is_not_overwritten_by_dbus_interface_name(self):
        a = Advertisement("LoStik")
        self.assertEqual(a.name, "org.bluez.LEAdvertisement1")
        self.assertEqual(a.LocalName, "LoStik")
        self.assertEqual(a.Type, "peripheral")
        self.assertTrue(a.Discoverable)
        self.assertEqual(a.ServiceUUIDs, ["6ba1b218-15a8-461f-9fa8-5dcae273eafd"])

    def test_android_service_contract_and_authenticated_permissions(self):
        app = Application(self.bridge)
        callback = next(
            m.fn
            for m in ServiceInterface._get_methods(app)
            if m.name == "GetManagedObjects"
        )
        objects = callback(app)
        self.assertEqual(len(objects), 4)
        self.assertIn("encrypt-authenticated-read", FLAGS["fromradio"])
        self.assertIn("encrypt-authenticated-write", FLAGS["toradio"])
        self.assertIn("notify", FLAGS["fromnum"])

    def test_dbus_properties_do_not_expose_radio_packets(self):
        self.bridge.mailbox.push(b"private radio packet")
        self.bridge.mailbox.read()
        self.assertEqual(self.bridge.chars["fromradio"].Value, b"")

    def test_unpaired_peer_cannot_open_native_api(self):
        async def props(path):
            return {
                "Paired": Variant("b", False),
                "Connected": Variant("b", True),
            }, None

        self.bridge.device_properties = props
        with self.assertRaises(DBusError):
            asyncio.run(self.bridge.ensure_session("/org/bluez/hci0/device_test"))
        self.assertFalse(self.bridge.args.lease.exists())
        self.assertIsNone(self.bridge.writer)

    def test_unauthenticated_pairing_is_not_automatically_accepted(self):
        with self.assertRaises(DBusError):
            Agent(self.bridge).RequestAuthorization("/org/bluez/hci0/device_test")

    def test_numeric_confirmation_requires_local_approval(self):
        self.bridge.args.confirmation_timeout = 0.15
        agent = Agent(self.bridge)
        callback = next(
            m.fn
            for m in ServiceInterface._get_methods(agent)
            if m.name == "RequestConfirmation"
        )
        with self.assertRaises(DBusError):
            asyncio.run(callback(agent, "/org/bluez/hci0/device_test", 123456))
        self.assertIsNone(self.bridge.confirmation_token)

    def test_numeric_confirmation_accepts_current_matching_number(self):
        self.bridge.args.confirmation_timeout = 1
        agent = Agent(self.bridge)
        callback = next(
            m.fn
            for m in ServiceInterface._get_methods(agent)
            if m.name == "RequestConfirmation"
        )

        async def scenario():
            task = asyncio.create_task(
                callback(agent, "/org/bluez/hci0/device_test", 123456)
            )
            await asyncio.sleep(0.02)
            state = json.loads(self.bridge.args.pairing.read_text())
            self.assertEqual(state["status"], "confirm_number_matches_phone")
            approval = self.bridge.args.pairing.with_suffix(".approved.json")
            approval.write_text(
                json.dumps({"token": state["token"], "code": state["passkey"]})
            )
            await task
            self.assertFalse(approval.exists())
            self.assertEqual(
                json.loads(self.bridge.args.pairing.read_text())["status"],
                "number_confirmed",
            )

        asyncio.run(scenario())

    def test_numeric_confirmation_rejects_stale_token_or_wrong_number(self):
        self.bridge.args.confirmation_timeout = 0.2
        agent = Agent(self.bridge)
        callback = next(
            m.fn
            for m in ServiceInterface._get_methods(agent)
            if m.name == "RequestConfirmation"
        )

        async def scenario():
            task = asyncio.create_task(
                callback(agent, "/org/bluez/hci0/device_test", 123456)
            )
            await asyncio.sleep(0.02)
            state = json.loads(self.bridge.args.pairing.read_text())
            approval = self.bridge.args.pairing.with_suffix(".approved.json")
            approval.write_text(
                json.dumps({"token": "stale", "code": state["passkey"]})
            )
            await asyncio.sleep(0.1)
            approval.write_text(json.dumps({"token": state["token"], "code": "654321"}))
            with self.assertRaises(DBusError):
                await task
            self.assertFalse(approval.exists())

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
