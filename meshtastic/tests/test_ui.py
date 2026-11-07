# SPDX-License-Identifier: GPL-3.0-or-later
import base64
import http.client
import importlib.util
import json
from pathlib import Path
import threading
import unittest

spec = importlib.util.spec_from_file_location(
    "portable_ui", Path(__file__).resolve().parents[1] / "tools/portable_ui.py"
)
ui = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ui)


class FakeRadio:
    csrf = "test-csrf"

    def __init__(self):
        self.sent = []

    def state(self):
        return {
            "connected": True,
            "csrf": self.csrf,
            "nodes": [],
            "channels": [],
            "messages": [],
        }

    def send(self, body):
        self.sent.append(body)


class SecurityTest(unittest.TestCase):
    def setUp(self):
        self.server = ui.ThreadingHTTPServer(("127.0.0.1", 0), ui.Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_port
        self.server.credentials = "tester:private-test-password"
        self.server.hosts = {"127.0.0.1:" + str(self.port)}
        self.server.radio = FakeRadio()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, path, method="GET", headers=None, body=None, auth=True):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
        h = dict(headers or {})
        if auth:
            h["Authorization"] = (
                "Basic " + base64.b64encode(self.server.credentials.encode()).decode()
            )
        c.request(method, path, body=body, headers=h)
        r = c.getresponse()
        result = (r.status, r.read(), dict(r.getheaders()))
        c.close()
        return result

    def test_unauthenticated_cannot_read_messages(self):
        self.assertEqual(self.request("/api/state", auth=False)[0], 401)

    def test_dns_rebinding_host_rejected(self):
        self.assertEqual(
            self.request("/api/state", headers={"Host": "attacker.invalid"})[0], 403
        )

    def test_cross_site_send_cannot_transmit(self):
        r = self.request(
            "/api/send",
            "POST",
            {
                "Origin": "https://attacker.invalid",
                "X-Mesh-CSRF": self.server.radio.csrf,
                "Content-Type": "application/json",
            },
            "{}",
        )
        self.assertEqual(r[0], 403)
        self.assertEqual(self.server.radio.sent, [])

    def test_missing_csrf_cannot_transmit(self):
        r = self.request(
            "/api/send",
            "POST",
            {
                "Origin": "http://127.0.0.1:" + str(self.port),
                "Content-Type": "application/json",
            },
            "{}",
        )
        self.assertEqual(r[0], 403)
        self.assertEqual(self.server.radio.sent, [])

    def test_authenticated_same_origin_send_and_no_cache(self):
        r = self.request("/api/state")
        self.assertEqual(r[0], 200)
        self.assertEqual(r[2]["Cache-Control"], "no-store")
        body = {"text": "test", "to": "^all", "channel": 0}
        r = self.request(
            "/api/send",
            "POST",
            {
                "Origin": "http://127.0.0.1:" + str(self.port),
                "Content-Type": "application/json",
                "X-Mesh-CSRF": self.server.radio.csrf,
            },
            json.dumps(body),
        )
        self.assertEqual(r[0], 202)
        self.assertEqual(self.server.radio.sent, [body])

    def test_unbounded_request_rejected(self):
        r = self.request(
            "/api/send",
            "POST",
            {
                "Origin": "http://127.0.0.1:" + str(self.port),
                "Content-Type": "application/json",
                "X-Mesh-CSRF": self.server.radio.csrf,
            },
            " " * 4097,
        )
        self.assertEqual(r[0], 400)
        self.assertEqual(self.server.radio.sent, [])


if __name__ == "__main__":
    unittest.main()
