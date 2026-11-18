"""Versioned public shop protocol with evaluator-owned, out-of-band evidence."""

import copy
import hashlib
import hmac
import json
import math
import socket
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from http.client import LineTooLong
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from socketserver import ThreadingUnixStreamServer
from urllib.parse import parse_qs, urlsplit


@dataclass(frozen=True)
class Profile:
    login_csrf: bool = False
    rotate_csrf: bool = False
    exclusive_accounts: bool = False
    expire_after: int = 1000
    require_etag: bool = False
    dynamic_etag: bool = False
    decline_orders: bool = False
    deny_orders: bool = False
    unexpected_every: int = 0
    lose_commit_response: bool = False
    fail_every: int = 0
    retry_after: float = .01
    delay_seconds: float = 0
    slow_after: int = 0
    slow_seconds: float = 0
    job_polls: int = 2
    job_fails: bool = False
    poll_interval: float = 0
    html_layout: int = 0
    eligible_index: int = 0
    reverse_catalog: bool = False
    require_challenge: bool = False
    login_redirect: bool = False
    challenge_ttl: float = 2
    etag_conflict_once: bool = False
    checkout_delay_seconds: float = 0
    catalog_delay_seconds: float = 0
    checkout_slow_after: int = 0
    checkout_slow_seconds: float = 0
    slow_after_seconds: float | None = None
    checkout_slow_after_seconds: float | None = None

    def __post_init__(self):
        for key in ("login_csrf", "rotate_csrf", "exclusive_accounts", "require_etag", "dynamic_etag",
                    "decline_orders", "deny_orders", "lose_commit_response", "job_fails", "reverse_catalog",
                    "require_challenge", "login_redirect", "etag_conflict_once"):
            if type(getattr(self, key)) is not bool:
                raise ValueError("Invalid profile flag")
        for key in ("expire_after", "fail_every", "unexpected_every", "slow_after", "job_polls", "checkout_slow_after"):
            value = getattr(self, key)
            if type(value) is not int or not 0 <= value <= 100000:
                raise ValueError("Invalid profile counter")
        if (type(self.html_layout) is not int or self.html_layout not in (0, 1)
                or type(self.eligible_index) is not int or self.eligible_index not in (0, 1, 2)):
            raise ValueError("Invalid fixture layout")
        for key in ("retry_after", "delay_seconds", "slow_seconds", "poll_interval",
                    "challenge_ttl", "checkout_delay_seconds", "checkout_slow_seconds", "catalog_delay_seconds"):
            value = getattr(self, key)
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or not 0 <= value <= 2):
                raise ValueError("Invalid profile delay")
        for value in (self.slow_after_seconds, self.checkout_slow_after_seconds):
            if value is not None and (isinstance(value, bool)
                                      or not isinstance(value, (int, float))
                                      or not math.isfinite(value) or not 0 <= value <= 300):
                raise ValueError("Invalid profile phase boundary")


class Shop:
    def __init__(self, profile, seed, clock=time.monotonic):
        self.profile, self.clock = profile, clock
        self._seed = str(seed).encode()
        self._lock = threading.Lock()
        self._idle = threading.Condition(self._lock)
        self._pending = 0
        self._counter = 0
        self._events = []
        self._sessions, self._forms, self._carts = {}, {}, {}
        self._orders, self._keys, self._jobs = {}, {}, {}
        self._challenges, self._claims = {}, {}
        self._rates = {}
        self._epochs = {}
        self.audit_overflow = False
        self.products = [{"id": self._id("product"), "variants": [
            {"id": self._id("variant"), "in_stock": i == profile.eligible_index}], "name": f"Item {i + 1}"}
            for i in range(3)]
        if profile.reverse_catalog:
            self.products.reverse()
        self._cursors = [self._id("cursor") for _ in self.products]

    def _id(self, kind):
        self._counter += 1
        return kind + "-" + hmac.new(self._seed, f"{kind}:{self._counter}".encode(),
                                    hashlib.sha256).hexdigest()[:20]

    def _event(self, **values):
        if len(self._events) >= 32768:
            self.audit_overflow = True
            return
        self._events.append({"sequence": len(self._events), "time": self.clock(), **values})

    def _audit_capacity(self, count):
        if self.audit_overflow or len(self._events) + self._pending + count > 32768:
            self.audit_overflow = True
            return False
        return True

    def audit(self, *, wait=True):
        with self._idle:
            if wait and self.audit_overflow:
                raise RuntimeError("Demo audit overflow; evidence is incomplete")
            if wait and not self._idle.wait_for(lambda: self._pending == 0, timeout=6):
                raise RuntimeError("Demo requests did not drain; audit is not final")
            if wait and self.audit_overflow:
                raise RuntimeError("Demo audit overflow; evidence is incomplete")
            return copy.deepcopy(self._events)

    def begin(self, method, path):
        with self._lock:
            if not self._audit_capacity(2):
                raise RuntimeError("Demo audit overflow; cannot admit request")
            self._pending += 1
            request = self._id("request")
            self._event(kind="start", request=request, method=method, path=path[:2048])
            return request

    def end(self, request, outcome, **values):
        with self._lock:
            self._event(kind="end", request=request, outcome=outcome, **values)
            self._pending -= 1
            self._idle.notify_all()

    def dispatch(self, method, target, headers, body, request):
        with self._lock:
            path = urlsplit(target).path
            budget = (0 if path in ("/v2/health", "/v2/login-form") else
                      1 if path == "/v2/login" and method == "POST" else 3)
            if not self._audit_capacity(budget) or max(map(len, (self._sessions, self._forms, self._carts,
                                                   self._orders, self._jobs, self._challenges,
                                                   self._claims))) >= 4096:
                return 503, {"error": "capacity"}, {}, 0, False
            return self._dispatch(method, target, headers, body, request)

    def service_delay(self, method, target, request):
        if method == "GET" and urlsplit(target).path == "/v2/catalog":
            return self.profile.catalog_delay_seconds
        if method != "POST" or urlsplit(target).path != "/v2/orders":
            return 0
        with self._lock:
            if not self._audit_capacity(1):
                return 0
            count = self._rates.get("/v2/orders", 0) + 1
            self._rates["/v2/orders"] = count
            slow = bool(self.profile.checkout_slow_after and count > self.profile.checkout_slow_after)
            if self.profile.checkout_slow_after_seconds is not None:
                epoch = self._epochs.setdefault("checkout", self.clock())
                slow = self.clock() - epoch >= self.profile.checkout_slow_after_seconds
            self._event(kind="service", request=request, phase="slow" if slow else "base")
            return (self.profile.checkout_slow_seconds if slow
                    else self.profile.checkout_delay_seconds)

    def _dispatch(self, method, target, headers, body, request):
        p = self.profile
        url = urlsplit(target)
        path = url.path
        cookie = SimpleCookie()
        try:
            cookie.load(headers.get("Cookie", ""))
        except (ValueError, CookieError):
            return 400, {"error": "cookie"}, {}, 0, False

        def reply(status, payload, extra=None, delay=0, lost=False):
            return status, payload, extra or {}, delay, lost

        def claim(session):
            token = self._id("claim")
            self._claims[token] = {"session": session, "used": False,
                                   "expires": self.clock() + p.challenge_ttl}
            return "/v2/claim/" + token

        if path == "/v2/health" and method == "GET":
            return reply(200, {"ok": True})

        if path == "/v2/login-form" and method == "GET":
            token = self._id("form")
            self._forms[token] = True
            actual = (f'<input value="{token}" type="hidden" name="csrf">' if not p.html_layout
                      else f"<input name='csrf' type='hidden' value='{token}'>")
            return reply(200, '<form><input type="hidden" value="decoy" name="unused">'
                         + actual + '</form>',
                         {"Set-Cookie": f"login_nonce={token}; Path=/; HttpOnly"})
        if path == "/v2/login" and method == "POST":
            actor = body.get("username")
            if actor not in ["bench", *[f"bench-{i}" for i in range(1, 17)]] or body.get(
                    "password") != "bench":
                return reply(401, {"error": "credentials"})
            if p.login_csrf:
                token = cookie.get("login_nonce")
                if (not token or token.value not in self._forms
                        or body.get("csrf") != token.value):
                    return reply(403, {"error": "login_csrf"})
                del self._forms[token.value]
            if p.exclusive_accounts:
                for state in self._sessions.values():
                    if state["actor"] == actor:
                        state["revoked"] = True
            sid = self._id("session")
            state = {"actor": actor, "csrf": self._id("csrf"), "operations": 0, "revoked": False}
            self._sessions[sid] = state
            self._event(kind="login", request=request, actor=actor, session=sid)
            extra = {"Set-Cookie": f"session={sid}; Path=/; HttpOnly"}
            if p.login_redirect:
                extra["Location"] = claim(sid)
            return reply(302 if p.login_redirect else 200,
                         {"csrf": state["csrf"], "user": actor}, extra)
        sid = cookie.get("session")
        sid = sid.value if sid else None
        state = self._sessions.get(sid)
        if not state or state["revoked"]:
            return reply(401, {"error": "session"})
        actor = state["actor"]
        self._event(kind="authenticated", request=request, actor=actor, session=sid,
                    operation=state["operations"])
        if path == "/v2/orders" and method == "POST":
            saved = self._keys.get((actor, headers.get("Idempotency-Key")), {})
            recovery = saved.get("recovery")
            if (recovery and recovery["session"] == sid and recovery["remaining"]
                    and self.clock() < recovery["expires"]
                    and headers.get("X-CSRF-Token") == recovery["previous"]
                    and state["csrf"] == recovery["current"]
                    and headers.get("If-Match") == recovery["etag"]
                    and json.dumps(body, sort_keys=True) == recovery["body"]):
                recovery["remaining"] = 0
                return reply(200, {"accepted": True, "id": saved["order"], "replayed": True,
                                   "csrf": state["csrf"]})
        if path == "/v2/session/renew" and method == "POST":
            if headers.get("X-CSRF-Token") != state["csrf"]:
                return reply(403, {"error": "csrf"})
            state.update(operations=0, csrf=self._id("csrf"))
            self._event(kind="renew", request=request, actor=actor, session=sid)
            return reply(200, {"csrf": state["csrf"]})
        if state["operations"] >= p.expire_after:
            return reply(401, {"error": "expired", "renew": "/v2/session/renew"})
        state["operations"] += 1
        if method != "GET" and headers.get("X-CSRF-Token") != state["csrf"]:
            return reply(403, {"error": "csrf"})
        if method != "GET":
            self._event(kind="mutation_authorized", request=request, actor=actor, session=sid)

        def mutation(payload, status=200, extra=None, lost=False):
            if p.rotate_csrf:
                state["csrf"] = self._id("csrf")
            return reply(status, {**payload, "csrf": state["csrf"]}, extra, lost=lost)

        if path == "/v2/challenge-form" and method == "GET":
            challenge = self._id("challenge")
            self._challenges[challenge] = {"session": sid, "used": False,
                                           "expires": self.clock() + p.challenge_ttl}
            return reply(200, f'<form action="/v2/challenges/{challenge}">'
                         f'<input name="challenge" type="hidden" value="{challenge}"></form>')
        if path.startswith("/v2/challenges/") and method == "POST":
            challenge = path.split("/")[-1]
            saved = self._challenges.get(challenge)
            if not saved or saved["session"] != sid or body.get("challenge") != challenge:
                return reply(403, {"error": "challenge"})
            if saved["used"] or self.clock() >= saved["expires"]:
                return reply(410, {"error": "challenge_consumed_or_expired"})
            saved["used"] = True
            return reply(302, {"ok": True}, {"Location": claim(sid)})
        if path.startswith("/v2/claim/") and method == "GET":
            token = path.split("/")[-1]
            saved = self._claims.get(token)
            if not saved or saved["session"] != sid:
                return reply(404, {"error": "claim"})
            if saved["used"] or self.clock() >= saved["expires"]:
                return reply(410, {"error": "claim_consumed_or_expired"})
            saved["used"] = True
            state["grant"] = token
            self._event(kind="claim", request=request, actor=actor, session=sid, resource=token)
            return reply(200, {"grant_id": token, "csrf": state["csrf"]})

        if path in ("/v2/catalog", "/v2/catalog/page", "/v2/catalog-html") and method == "GET":
            cursor = parse_qs(url.query).get("cursor", [self._cursors[0]])[0]
            if cursor not in self._cursors:
                return reply(400, {"error": "cursor"})
            index = self._cursors.index(cursor)
            next_url = f"/v2/catalog/page?cursor={self._cursors[index + 1]}" if index < 2 else None
            if path.endswith("-html"):
                next_url = next_url.replace("/v2/catalog/page", "/v2/catalog-html") if next_url else None
                return reply(200, '<article data-product-id="' + self.products[index]["id"]
                             + '"></article>' + (f'<a rel="next" href="{next_url}">Next</a>'
                                                if next_url else ""))
            return reply(200, {"data": {"products": (self.products if path == "/v2/catalog"
                                                    else [self.products[index]])},
                               "next": None if path == "/v2/catalog" else next_url})
        if path == "/v2/carts" and method == "POST":
            product = next((x for x in self.products if x["id"] == body.get("product_id")), None)
            if not product or body.get("variant_id") != product["variants"][0]["id"]:
                return reply(400, {"error": "product"})
            if p.require_challenge:
                grant = state.get("grant")
                if (not grant or body.get("grant_id") != grant
                        or self.clock() >= self._claims[grant]["expires"]):
                    return reply(403, {"error": "grant"})
                del state["grant"]
            cart = self._id("cart")
            revision = int(self._id("revision").split("-")[1], 16) % 1000000 + 2 if p.dynamic_etag else 1
            self._carts[cart] = {"actor": actor, "session": sid, "etag": revision,
                                 "in_stock": product["variants"][0]["in_stock"], "ordered": False,
                                 "quantity": 1, "product": product["id"],
                                 "variant": product["variants"][0]["id"]}
            self._event(kind="cart", request=request, actor=actor, session=sid, resource=cart,
                        grant=grant if p.require_challenge else None)
            return mutation({"id": cart}, 201, {"ETag": f'"{revision}"'})
        if path.startswith("/v2/carts/"):
            cart_id = path.split("/")[3]
            cart = self._carts.get(cart_id)
            if not cart or cart["actor"] != actor:
                return reply(404, {"error": "cart"})
            if path.endswith("/recover") and method == "POST":
                cart["session"] = sid
                return mutation({"id": cart_id}, extra={"ETag": f'"{cart["etag"]}"'})
            if cart["session"] != sid:
                return reply(409, {"error": "recover_cart"})
            if method == "GET":
                return reply(200, {"id": cart_id}, {"ETag": f'"{cart["etag"]}"'})
            if method == "PATCH":
                if headers.get("If-Match") != f'"{cart["etag"]}"':
                    return reply(412, {"error": "etag"})
                if type(body.get("quantity")) is not int or not 1 <= body["quantity"] <= 20:
                    return reply(400, {"error": "quantity"})
                if p.etag_conflict_once and not cart.get("conflicted"):
                    cart.update(conflicted=True, etag=cart["etag"] + 1)
                    self._event(kind="cart_conflict", request=request, actor=actor,
                                session=sid, resource=cart_id, version=cart["etag"])
                    return reply(412, {"error": "etag_conflict"})
                cart["etag"] += 1
                cart["quantity"] = body["quantity"]
                self._event(kind="cart_update", request=request, actor=actor, session=sid,
                            resource=cart_id, version=cart["etag"], quantity=cart["quantity"])
                return mutation({"id": cart_id}, extra={"ETag": f'"{cart["etag"]}"'})
        if path == "/v2/reservations" and method == "POST":
            cart = self._carts.get(body.get("cart_id"))
            if not cart or cart["actor"] != actor or cart["session"] != sid or cart["ordered"]:
                return reply(409, {"error": "cart"})
            job = self._id("job")
            self._jobs[job] = {"actor": actor, "cart": body["cart_id"], "polls": 0,
                               "last": None, "used": False, "reservation": self._id("reservation")}
            self._event(kind="reservation", request=request, actor=actor, session=sid,
                        resource=body["cart_id"], job=job)
            return mutation({"status_url": f"/v2/jobs/{job}"}, 202)
        if path.startswith("/v2/jobs/") and method == "GET":
            job = self._jobs.get(path.split("/")[-1])
            if not job or job["actor"] != actor:
                return reply(404, {"error": "job"})
            now = self.clock()
            if job["last"] is not None and now - job["last"] < p.poll_interval:
                return reply(429, {"error": "poll_interval"},
                             {"Retry-After": str(math.ceil(p.poll_interval))})
            job["last"], job["polls"] = now, job["polls"] + 1
            ready = job["polls"] >= p.job_polls
            phase = ("failed" if p.job_fails else "ready") if ready else "pending"
            self._event(kind="job_state", request=request, actor=actor, session=sid,
                        resource=path.split("/")[-1], phase=phase,
                        reservation=job["reservation"] if phase == "ready" else None)
            return reply(200, {"status": phase, **({"reservation_id": job["reservation"]}
                                                  if phase == "ready" else {})})
        if path == "/v2/orders" and method == "POST":
            if p.deny_orders:
                return reply(401, {"error": "denied"})
            key = headers.get("Idempotency-Key")
            if not key or len(key) > 128:
                return reply(400, {"error": "idempotency_key"})
            cart_id = body.get("cart_id")
            saved = self._keys.get((actor, key))
            if saved:
                if saved["cart"] != cart_id:
                    return reply(409, {"error": "idempotency_conflict"})
                return mutation({"accepted": True, "id": saved["order"], "replayed": True})
            cart = self._carts.get(cart_id)
            if not cart or cart["actor"] != actor or cart["session"] != sid or cart["ordered"]:
                return reply(409, {"error": "cart"})
            if p.require_etag and headers.get("If-Match") != f'"{cart["etag"]}"':
                return reply(412, {"error": "etag"})
            job = None
            if "reservation_id" in body:
                job = next((j for j in self._jobs.values()
                            if j["reservation"] == body["reservation_id"]), None)
                if (not job or job["actor"] != actor or job["cart"] != cart_id or job["used"]
                        or job["polls"] < p.job_polls or p.job_fails):
                    return reply(409, {"error": "reservation"})
            if p.decline_orders or not cart["in_stock"]:
                self._event(kind="decline", request=request, actor=actor, session=sid,
                            resource=cart_id)
                return reply(200, {"accepted": False, "error": "business_decline"})
            order = self._id("order")
            self._orders[order] = {"actor": actor, "cart": cart_id}
            self._keys[(actor, key)] = {"order": order, "cart": cart_id}
            cart["ordered"] = True
            if job:
                job["used"] = True
            self._event(kind="commit", request=request, actor=actor, session=sid,
                        resource=cart_id, order=order, idempotency_key=key,
                        quantity=cart["quantity"], product=cart["product"], variant=cart["variant"],
                        reservation=body.get("reservation_id"))
            previous = state["csrf"]
            response = mutation({"accepted": True, "id": order}, 201, lost=p.lose_commit_response)
            if p.rotate_csrf and p.lose_commit_response:
                self._keys[(actor, key)]["recovery"] = {
                    "session": sid, "previous": previous, "current": state["csrf"],
                    "body": json.dumps(body, sort_keys=True), "etag": headers.get("If-Match"),
                    "expires": self.clock() + 10, "remaining": 1}
            return response
        if path in ("/v2/browse", "/v2/search", "/v2/work") and method in ("GET", "POST"):
            count = self._rates.get(path, 0) + 1
            self._rates[path] = count
            slow = bool(p.slow_after and count > p.slow_after)
            if p.slow_after_seconds is not None:
                epoch = self._epochs.setdefault("work", self.clock())
                slow = self.clock() - epoch >= p.slow_after_seconds
            fault = bool(p.fail_every and count % p.fail_every == 0)
            unexpected = bool(p.unexpected_every and count % p.unexpected_every == 0)
            self._event(kind="work", request=request, actor=actor, session=sid,
                        phase="slow" if slow else "base", fault="unexpected" if unexpected else "rate_limit" if fault else None)
            if unexpected:
                return reply(500, {"error": "unexpected"})
            return reply(429 if fault else 200, {"ok": not fault},
                         {"Retry-After": str(math.ceil(p.retry_after))} if fault else {},
                         p.slow_seconds if slow else p.delay_seconds)
        return reply(404, {"error": "route"})


@contextmanager
def demo_app_v2(profile=None, seed="public-development", *, socket_path=None, clock=time.monotonic):
    shop = Shop(profile or Profile(), seed, clock)
    slots = threading.BoundedSemaphore(64)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def setup(self):
            super().setup()
            self.connection.settimeout(3)
            stream = self.rfile

            class Input:
                def __getattr__(self, name):
                    return getattr(stream, name)

                def readline(self, size=8193):
                    line = stream.readline(min(size, 8193))
                    if len(line) > 8192:
                        raise LineTooLong("request line/header")
                    return line

            self.rfile = Input()

        def handle_one_request(self):
            try:
                super().handle_one_request()
            except LineTooLong:
                self.close_connection = True

        def do_GET(self):
            self.handle_request()

        do_POST = do_PATCH = do_GET

        def handle_request(self):
            request = shop.begin(self.command, self.path)
            status, lost, payload = 400, False, {}
            try:
                try:
                    length = int(self.headers.get("Content-Length", 0))
                    if self.headers.get("Transfer-Encoding") or not 0 <= length <= 4096:
                        raise ValueError("body length")
                    body = json.loads(self.rfile.read(length)) if length else {}
                    if (not isinstance(body, dict)
                            or any(isinstance(value, (dict, list)) for value in body.values())):
                        raise ValueError("body object")
                    service_delay = shop.service_delay(self.command, self.path, request)
                    if service_delay:
                        time.sleep(service_delay)
                    status, payload, headers, delay, lost = shop.dispatch(
                        self.command, self.path, self.headers, body, request)
                except (ValueError, TimeoutError):
                    status, payload, headers, delay = 400, {"error": "invalid body"}, {}, 0
                if delay:
                    time.sleep(delay)
                if lost:
                    self.close_connection = True
                    self.connection.shutdown(socket.SHUT_RDWR)
                    return
                data = payload.encode() if isinstance(payload, str) else json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("X-P95-Request-ID", request)
                self.send_header("Content-Type", "text/html" if isinstance(payload, str)
                                 else "application/json")
                self.send_header("Content-Length", str(len(data)))
                for key, value in headers.items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(data)
            except OSError:
                pass
            finally:
                shop.end(request, status, response_lost=lost,
                         error=payload.get("error") if isinstance(payload, dict) else None)

    base = ThreadingUnixStreamServer if socket_path else ThreadingHTTPServer

    class Server(base):
        daemon_threads = True

        def process_request(self, request, address):
            if not slots.acquire(blocking=False):
                request.close()
                return
            try:
                super().process_request(request, address)
            except BaseException:
                slots.release()
                raise

        def process_request_thread(self, request, address):
            try:
                super().process_request_thread(request, address)
            finally:
                slots.release()

    server = Server(str(socket_path) if socket_path else ("127.0.0.1", 0), Handler)
    if socket_path:
        socket_path.chmod(0o666)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield shop, (socket_path if socket_path else f"http://127.0.0.1:{server.server_port}")
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
        if socket_path:
            socket_path.unlink(missing_ok=True)
