"""Ephemeral loopback-only shop with per-session flow verification."""

import json
import secrets
import threading
from contextlib import contextmanager
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


@contextmanager
def demo_app():
    sessions = {}
    lock = threading.Lock()
    admin = secrets.token_hex(24)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, status, body, cookie=None):
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            if cookie:
                self.send_header("Set-Cookie", f"session={cookie}; Path=/; HttpOnly")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self.handle_request()

        def do_POST(self):
            self.handle_request()

        def handle_request(self):
            with lock:
                if self.path == "/internal/stats":
                    if self.headers.get("X-Admin-Key") != admin:
                        return self.reply(403, {"error": "internal"})
                    return self.reply(200, {"completions": sum(
                        s["done"] for s in sessions.values())})
                try:
                    length = int(self.headers.get("Content-Length", 0))
                    if length > 4096:
                        return self.reply(413, {"error": "too large"})
                    body = json.loads(self.rfile.read(length)) if length else {}
                except (ValueError, json.JSONDecodeError):
                    return self.reply(400, {"error": "invalid JSON"})
                if self.path == "/login" and self.command == "POST":
                    if body != {"username": "bench", "password": "bench"}:
                        return self.reply(401, {"error": "credentials"})
                    sid, token = secrets.token_hex(16), secrets.token_hex(16)
                    sessions[sid] = {"token": token, "listed": False,
                                     "cart": False, "done": False}
                    return self.reply(200, {"csrf": token}, sid)
                cookie = SimpleCookie()
                try:
                    cookie.load(self.headers.get("Cookie", ""))
                    sid = cookie["session"].value
                    state = sessions[sid]
                except (KeyError, ValueError):
                    return self.reply(401, {"error": "session"})
                if self.path == "/products" and self.command == "GET":
                    state["listed"] = True
                    return self.reply(200, {"products": [{"id": "sku-1", "price": 10}]})
                if self.path in ("/cart", "/checkout") and self.command == "POST":
                    if self.headers.get("X-CSRF-Token") != state["token"]:
                        return self.reply(403, {"error": "csrf"})
                    if self.path == "/cart" and state["listed"] and body == {"product_id": "sku-1"}:
                        state["cart"] = True
                        return self.reply(200, {"added": True})
                    if self.path == "/checkout" and state["cart"] and not state["done"]:
                        state["done"] = True
                        return self.reply(200, {"completed": True})
                    return self.reply(409, {"error": "flow"})
                return self.reply(404, {"error": "route"})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", admin
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
