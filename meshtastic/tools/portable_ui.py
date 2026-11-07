#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Small local Meshtastic UI for a Pi. Reach it over an SSH port forward."""
import argparse
import base64
from collections import deque
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import threading
import time

HTML = """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>LoStik Mesh</title>
<style>body{font:16px system-ui;background:#101923;color:#edf4fa;max-width:1050px;margin:30px auto;padding:15px}h1{color:#65e3ba}section{background:#1c2a38;border-radius:12px;padding:18px;margin:20px 0}input,select,button{padding:10px;margin:4px;border-radius:6px;max-width:92%}button{cursor:pointer;background:#65e3ba;color:#10251f;border:0}table{border-collapse:collapse;width:100%;font-size:.9em}th,td{padding:8px;border-bottom:1px solid #36495a;text-align:left}small,#status{color:#b3cbdc}.scroll{overflow:auto}svg{width:100%;height:250px;background:#10202d}text{fill:#edf4fa;font:12px system-ui}</style>
<h1>LoStik Mesh</h1><p id="status">Connecting to the local radio…</p><p><small>Experimental RN2903 host adapter · Messages use the selected radio channel. Keep the Pi and radio powered together.</small></p>
<section><h2>Send a message</h2><label>Channel <select id="channel"></select></label><label>To <select id="to"><option value="^all">Channel broadcast</option></select></label><br><input id="message" maxlength="220" size="70" placeholder="Message"><button id="send">Send</button><p id="result" role="status"></p></section>
<section><h2>Nodes</h2><p><small>These are nodes actually reported by the radio. Sleeping and distant nodes may disappear. A LoStik has no built-in GPS.</small></p><div class="scroll"><table><thead><tr><th>Name</th><th>ID</th><th>Last heard</th><th>Position</th></tr></thead><tbody id="nodes"></tbody></table></div><svg id="positions" viewBox="0 0 900 250" aria-label="Relative node positions"></svg><small>Relative positions only; no Internet map downloads are required.</small></section>
<section><h2>Recent messages</h2><div class="scroll"><table><thead><tr><th>Time</th><th>From</th><th>Text</th><th>Signal</th></tr></thead><tbody id="messages"></tbody></table></div></section>
<script>
let csrf='',lastOptions='';const el=id=>document.getElementById(id);function cell(row,text){const td=document.createElement('td');td.textContent=text;row.append(td)}
async function refresh(){try{const r=await fetch('/api/state',{cache:'no-store'});if(!r.ok)throw Error('Sign in again');const s=await r.json();csrf=s.csrf;el('status').textContent=s.connected?'Radio connected · '+s.name:(s.bluetooth_active?'Bluetooth app controls the radio · browser paused':'Radio disconnected · reconnecting');el('nodes').replaceChildren();for(const n of s.nodes){const row=document.createElement('tr');cell(row,n.name);cell(row,n.id);cell(row,n.lastHeard?new Date(n.lastHeard*1000).toLocaleString():'unknown');cell(row,n.position?n.position.join(', '):'not reported');el('nodes').append(row)}
const key=JSON.stringify([s.channels,s.nodes.map(n=>[n.id,n.name])]);if(key!==lastOptions){lastOptions=key;el('channel').replaceChildren();for(const c of s.channels){el('channel').append(new Option(c.name||('Channel '+c.index),c.index))}const prev=el('to').value;el('to').replaceChildren(new Option('Channel broadcast','^all'));for(const n of s.nodes)el('to').append(new Option(n.name+' '+n.id,n.id));if([...el('to').options].some(o=>o.value===prev))el('to').value=prev}
el('messages').replaceChildren();for(const m of s.messages.slice().reverse()){const row=document.createElement('tr');cell(row,new Date(m.time*1000).toLocaleTimeString());cell(row,m.from);cell(row,m.text);cell(row,m.signal||'');el('messages').append(row)}
const svg=el('positions');svg.replaceChildren();const points=s.nodes.filter(n=>n.position);if(points.length){const xs=points.map(n=>n.position[1]),ys=points.map(n=>n.position[0]);const x0=Math.min(...xs),y0=Math.min(...ys),dx=Math.max(.001,Math.max(...xs)-x0),dy=Math.max(.001,Math.max(...ys)-y0);for(const n of points){const x=30+810*(n.position[1]-x0)/dx,y=215-180*(n.position[0]-y0)/dy;const dot=document.createElementNS('http://www.w3.org/2000/svg','circle');dot.setAttribute('cx',x);dot.setAttribute('cy',y);dot.setAttribute('r',6);dot.setAttribute('fill','#65e3ba');svg.append(dot);const label=document.createElementNS(dot.namespaceURI,'text');label.setAttribute('x',x+10);label.setAttribute('y',y-10);label.textContent=n.name;svg.append(label)}}
}catch(e){el('status').textContent=e.message}}
el('send').onclick=async()=>{const b=el('send');b.disabled=true;try{const r=await fetch('/api/send',{method:'POST',headers:{'Content-Type':'application/json','X-Mesh-CSRF':csrf},body:JSON.stringify({text:el('message').value,to:el('to').value,channel:Number(el('channel').value)})});const x=await r.json();el('result').textContent=x.error||'Queued for radio delivery; an acknowledgment is separate.';if(r.ok)el('message').value=''}catch(e){el('result').textContent=e.message}finally{b.disabled=false}};refresh();setInterval(refresh,4000);
</script></html>"""


class Radio:
    def __init__(self, host, port, lease=None):
        self.host, self.port = host, port
        self.interface = None
        self.lock = threading.RLock()
        self.messages = deque(maxlen=200)
        self.last_send = 0.0
        self.csrf = secrets.token_urlsafe(32)
        self.lease = lease
        self.cached = None

    def bluetooth_active(self):
        try:
            return bool(self.lease and time.time() - self.lease.stat().st_mtime < 15)
        except FileNotFoundError:
            return False

    def received(self, packet, interface):
        if interface is not self.interface:
            return
        decoded = packet.get("decoded", {})
        if decoded.get("portnum") != "TEXT_MESSAGE_APP":
            return
        text = decoded.get("text")
        if text is None:
            return
        with self.lock:
            self.messages.append(
                {
                    "time": int(time.time()),
                    "from": packet.get("fromId", str(packet.get("from", "?"))),
                    "text": str(text)[:500],
                    "signal": f"{packet.get('rxRssi','?')} dBm / {packet.get('rxSnr','?')} dB",
                }
            )

    def run(self):
        import logging

        logging.disable(
            logging.CRITICAL
        )  # Never write decrypted radio traffic to service logs.
        from meshtastic.tcp_interface import TCPInterface
        from pubsub import pub

        pub.subscribe(self.received, "meshtastic.receive")
        while True:
            if self.bluetooth_active():
                time.sleep(0.25)
                continue
            try:
                interface = TCPInterface(hostname=self.host, portNumber=self.port)
                with self.lock:
                    self.interface = interface
                while interface.isConnected.is_set():
                    time.sleep(0.25)
                    if self.bluetooth_active() or getattr(
                        interface, "_wantExit", False
                    ):
                        break
            except Exception:
                pass
            finally:
                with self.lock:
                    if self.interface:
                        self.cached = self.state()
                    old, self.interface = self.interface, None
                if old:
                    try:
                        old.close()
                    except Exception:
                        pass
            time.sleep(5)

    def state(self):
        with self.lock:
            interface = self.interface
            result = {
                "connected": bool(interface and interface.isConnected.is_set()),
                "name": "LoStik",
                "nodes": [],
                "channels": [],
                "messages": list(self.messages),
                "csrf": self.csrf,
            }
            if not interface:
                if self.cached:
                    result.update(
                        {k: self.cached[k] for k in ["name", "nodes", "channels"]}
                    )
                result["bluetooth_active"] = self.bluetooth_active()
                return result
            for entry in list((interface.nodes or {}).values()):
                user = entry.get("user", {})
                position = entry.get("position", {})
                lat = position.get("latitude")
                lon = position.get("longitude")
                if lat is None and "latitudeI" in position:
                    lat = position["latitudeI"] / 1e7
                    lon = position["longitudeI"] / 1e7
                coords = (
                    [round(lat, 6), round(lon, 6)]
                    if isinstance(lat, (int, float))
                    and isinstance(lon, (int, float))
                    and -90 <= lat <= 90
                    and -180 <= lon <= 180
                    else None
                )
                result["nodes"].append(
                    {
                        "id": user.get("id", "!%08x" % entry.get("num", 0)),
                        "name": user.get("longName", "Unknown"),
                        "lastHeard": entry.get("lastHeard", 0),
                        "position": coords,
                    }
                )
            me = interface.getMyNodeInfo() or {}
            result["name"] = me.get("user", {}).get("longName", "LoStik")
            result["channels"] = [
                {"index": c.index, "name": c.settings.name}
                for c in interface.localNode.channels
                if c.role
            ]
            return result

    def send(self, body):
        text = body.get("text")
        destination = body.get("to", "^all")
        channel = body.get("channel", 0)
        if (
            not isinstance(text, str)
            or not text.strip()
            or len(text.encode("utf-8")) > 220
        ):
            raise ValueError("Enter 1–220 UTF-8 bytes of text")
        import re

        if destination != "^all" and not re.fullmatch(
            r"![0-9a-fA-F]{8}", str(destination)
        ):
            raise ValueError("Choose a node or channel broadcast")
        if (
            not isinstance(channel, int)
            or isinstance(channel, bool)
            or not 0 <= channel <= 7
        ):
            raise ValueError("Invalid channel")
        with self.lock:
            if not self.interface:
                raise ValueError("Radio disconnected")
            if channel not in [
                c.index for c in self.interface.localNode.channels if c.role
            ]:
                raise ValueError("Channel disabled")
            if time.monotonic() - self.last_send < 10:
                raise ValueError("Wait 10 seconds between messages")
            self.interface.sendText(
                text,
                destinationId=destination,
                wantAck=destination != "^all",
                channelIndex=channel,
            )
            self.last_send = time.monotonic()
            self.messages.append(
                {
                    "time": int(time.time()),
                    "from": "You → " + destination,
                    "text": text,
                    "signal": "queued",
                }
            )


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, status, body, kind="application/json"):
        data = (body if isinstance(body, str) else json.dumps(body)).encode()
        self.send_response(status)
        self.send_header("Content-Type", kind + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(data)

    def authorized(self):
        expected = (
            "Basic " + base64.b64encode(self.server.credentials.encode()).decode()
        )
        if not hmac.compare_digest(self.headers.get("Authorization", ""), expected):
            self.send_response(401)
            self.send_header(
                "WWW-Authenticate", 'Basic realm="LoStik Mesh", charset="UTF-8"'
            )
            self.send_header("Content-Length", "0")
            self.end_headers()
            return False
        if self.headers.get("Host", "") not in self.server.hosts:
            self.reply(403, {"error": "Invalid host"})
            return False
        return True

    def do_GET(self):
        if not self.authorized():
            return
        if self.path == "/":
            self.reply(200, HTML, "text/html")
        elif self.path == "/api/state":
            self.reply(200, self.server.radio.state())
        else:
            self.reply(404, {"error": "Not found"})

    def do_POST(self):
        if not self.authorized():
            return
        if self.path != "/api/send":
            self.reply(404, {"error": "Not found"})
            return
        if self.headers.get("Origin") not in {
            "http://" + h for h in self.server.hosts
        } or not hmac.compare_digest(
            self.headers.get("X-Mesh-CSRF", ""), self.server.radio.csrf
        ):
            self.reply(403, {"error": "Invalid request origin or CSRF token"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if (
                not 0 < length <= 4096
                or self.headers.get("Content-Type") != "application/json"
            ):
                raise ValueError("Expected a small JSON request")
            body = json.loads(self.rfile.read(length))
            self.server.radio.send(body)
            self.reply(202, {"queued": True})
        except (ValueError, TypeError, AttributeError):
            self.reply(
                400,
                {
                    "error": "Invalid message, disconnected radio, or sending too quickly"
                },
            )
        except Exception:
            self.reply(503, {"error": "Radio unavailable"})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--radio-port", type=int, default=4403)
    args = parser.parse_args()
    if args.credentials.stat().st_mode & 0o077:
        parser.error("Credentials must have mode 0600")
    credentials = json.loads(args.credentials.read_text())
    assert ":" not in credentials["username"]
    radio = Radio("127.0.0.1", args.radio_port, Path(__file__).with_name("ble-active"))
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.daemon_threads = True
    server.credentials = credentials["username"] + ":" + credentials["password"]
    server.radio = radio
    server.hosts = {"localhost:" + str(args.port), "127.0.0.1:" + str(args.port)}
    threading.Thread(target=radio.run, daemon=True).start()
    print("LoStik local UI ready on loopback; access with SSH forwarding", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
