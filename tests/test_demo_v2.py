import json
import re
import socket
import struct
import threading
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from http.client import RemoteDisconnected
from http.cookiejar import CookieJar
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPCookieProcessor, HTTPRedirectHandler, Request, build_opener

import pytest

from p95bench.demo import demo_app
from p95bench.demo_v2 import Profile, Shop, demo_app_v2


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


class Form(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.fields, self.action = {}, None
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self.action = attrs.get("action")
        if tag == "input":
            self.fields[attrs["name"]] = attrs["value"]


class Client:
    def __init__(self, url, *, follow_redirects=True):
        self.url, self.csrf = url, ""
        self.opener = build_opener(HTTPCookieProcessor(CookieJar()),
                                  *([] if follow_redirects else [NoRedirect()]))

    def request(self, path, method="GET", body=None, headers=None):
        payload = json.dumps(body or {}).encode() if method != "GET" else None
        request = Request(self.url + path, data=payload, method=method,
                          headers={"X-CSRF-Token": self.csrf, **(headers or {})})
        try:
            response = self.opener.open(request, timeout=3)
        except HTTPError as error:
            response = error
        with response:
            value = response.read().decode()
            if response.headers.get_content_type() == "application/json":
                value = json.loads(value)
            if isinstance(value, dict):
                self.csrf = value.get("csrf", self.csrf)
            return response.code, value, response.headers

    def login(self, actor="bench", **extra):
        return self.request("/v2/login", "POST", {"username": actor, "password": "bench", **extra})

    def cart(self):
        product = self.request("/v2/catalog")[1]["data"]["products"][0]
        status, value, _ = self.request("/v2/carts", "POST", {
            "product_id": product["id"], "variant_id": product["variants"][0]["id"]})
        assert status == 201
        return value["id"]


@pytest.mark.parametrize("spare", range(8))
@pytest.mark.parametrize("inflight", [False, True])
def test_audit_reserves_checkout_and_inflight_end_capacity(spare, inflight):
    with demo_app_v2() as (shop, url):
        client = Client(url)
        cookie = client.login()[2]["Set-Cookie"].split(";")[0]
        cart = client.cart()
        shop.audit()
        earlier = shop.begin("GET", "/v2/health") if inflight else None
        shop._events.extend([{"kind": "padding"}] * (32768 - spare - len(shop._events)))
        request = None
        try:
            request = shop.begin("POST", "/v2/orders")
            shop.service_delay("POST", "/v2/orders", request)
            shop.dispatch("POST", "/v2/orders", {"Cookie": cookie,
                "X-CSRF-Token": client.csrf, "Idempotency-Key": "boundary"},
                {"cart_id": cart}, request)
        except RuntimeError:
            assert shop.audit_overflow
        finally:
            if request:
                shop.end(request, 200)
            if earlier:
                shop.end(earlier, 200)
        expected = int(spare >= 6 + inflight)
        assert len(shop._orders) == expected
        assert sum(event["kind"] == "commit" for event in shop._events) == expected
        assert shop._pending == 0
        if expected:
            assert not shop.audit_overflow
            shop.audit()
        else:
            with pytest.raises(RuntimeError, match="overflow"):
                shop.audit()


def test_v2_seeded_catalog_html_csrf_and_audit_boundary():
    assert Shop(Profile(), 42).products == Shop(Profile(), 42).products
    assert Shop(Profile(), 42).products != Shop(Profile(), 43).products
    with demo_app_v2(Profile(login_csrf=True), seed=42) as (shop, url):
        client = Client(url)
        assert client.request("/v2/catalog")[0] == 401
        assert client.login()[0] == 403
        html = client.request("/v2/login-form")[1]
        token = re.search(r'value="([^"]+)" type="hidden" name="csrf"', html)[1]
        assert client.login(csrf=token)[0] == 200
        assert client.login(csrf=token)[0] == 403
        assert client.request("/v2/catalog?cursor=invalid")[0] == 400
        first = client.request("/v2/catalog/page")[1]
        second = client.request(first["next"])[1]
        assert first["data"] != second["data"]
        assert "data-product-id" in client.request("/v2/catalog-html")[1]
        for path in ("/internal/stats", "/audit", "/reset", "/v2/control"):
            assert client.request(path)[0] == 404
        events = shop.audit()
        assert [e["sequence"] for e in events] == list(range(len(events)))
        events[0]["kind"] = "forged"
        assert shop.audit()[0]["kind"] == "start"


def test_rotation_etag_ownership_and_exactly_once_commits():
    with demo_app_v2(Profile(rotate_csrf=True, require_etag=True)) as (shop, url):
        a, b = Client(url), Client(url)
        a.login()
        b.login("bench-1")
        old_csrf = a.csrf
        cart = a.cart()
        assert a.csrf != old_csrf
        assert b.request(f"/v2/carts/{cart}")[0] == 404
        assert b.request(f"/v2/carts/{cart}/recover", "POST")[0] == 404
        assert a.request(f"/v2/carts/{cart}", "PATCH", {"quantity": 2},
                         {"X-CSRF-Token": old_csrf})[0] == 403
        assert a.request(f"/v2/carts/{cart}", "PATCH", {"quantity": 2})[0] == 412
        assert a.request(f"/v2/carts/{cart}", "PATCH", {"quantity": 2}, {"If-Match": '"1"'})[0] == 200
        headers = {"Idempotency-Key": "one", "If-Match": '"1"', "X-Actor": "forged"}
        assert a.request("/v2/orders", "POST", {"cart_id": cart}, headers)[0] == 412
        headers["If-Match"] = '"2"'
        order = a.request("/v2/orders", "POST", {"cart_id": cart}, headers)
        assert order[0] == 201
        replay = a.request("/v2/orders", "POST", {"cart_id": cart}, headers)
        assert replay[1]["id"] == order[1]["id"] and replay[1]["replayed"]
        commits = [e for e in shop.audit() if e["kind"] == "commit"]
        assert len(commits) == 1 and commits[0]["actor"] == "bench"


def test_expiry_renewal_exclusive_accounts_and_documented_recovery():
    with demo_app_v2(Profile(expire_after=2, exclusive_accounts=True)) as (shop, url):
        a, b = Client(url), Client(url)
        a.login()
        cart = a.cart()
        assert a.request(f"/v2/carts/{cart}")[1]["error"] == "expired"
        assert a.request("/v2/session/renew", "POST")[0] == 200
        assert a.request(f"/v2/carts/{cart}")[0] == 200
        b.login()
        assert a.request("/v2/catalog")[0] == 401
        assert b.request(f"/v2/carts/{cart}")[1]["error"] == "recover_cart"
        assert b.request(f"/v2/carts/{cart}/recover", "POST")[0] == 200
        assert any(e["kind"] == "renew" for e in shop.audit())


@pytest.mark.parametrize("decline", [False, True])
def test_lost_response_retry_and_business_decline(decline):
    with demo_app_v2(Profile(lose_commit_response=True, decline_orders=decline)) as (shop, url):
        client = Client(url)
        client.login()
        cart = client.cart()
        if decline:
            response = client.request("/v2/orders", "POST", {"cart_id": cart},
                                      {"Idempotency-Key": "one"})
            assert response[0] == 200 and not response[1]["accepted"]
            assert not any(e["kind"] == "commit" for e in shop.audit())
        else:
            with pytest.raises(RemoteDisconnected):
                client.request("/v2/orders", "POST", {"cart_id": cart}, {"Idempotency-Key": "one"})
            replay = client.request("/v2/orders", "POST", {"cart_id": cart}, {"Idempotency-Key": "one"})
            assert replay[0] == 200 and replay[1]["replayed"]
            assert len([e for e in shop.audit() if e["kind"] == "commit"]) == 1
            assert any(e.get("response_lost") for e in shop.audit())


@pytest.mark.parametrize("expired", [False, True])
def test_rotated_csrf_lost_response_has_one_exact_bounded_replay(expired):
    now = [100.0]
    with demo_app_v2(Profile(rotate_csrf=True, lose_commit_response=True),
                     clock=lambda: now[0]) as (shop, url):
        client = Client(url)
        client.login()
        cart = client.cart()
        previous = client.csrf
        payload, headers = {"cart_id": cart}, {"Idempotency-Key": "lost"}
        with pytest.raises(RemoteDisconnected):
            client.request("/v2/orders", "POST", payload, headers)
        assert client.request("/v2/session/renew", "POST")[0] == 403
        assert client.request("/v2/orders", "POST", {**payload, "extra": True}, headers)[0] == 403
        assert client.request("/v2/orders", "POST", payload,
                              {**headers, "If-Match": '"changed"'})[0] == 403
        other = Client(url)
        other.login()
        assert other.request("/v2/orders", "POST", payload,
                             {**headers, "X-CSRF-Token": previous})[0] == 403
        if expired:
            now[0] += 11
        replay = client.request("/v2/orders", "POST", payload, headers)
        assert replay[0] == (403 if expired else 200)
        if not expired:
            assert replay[1]["replayed"] and client.csrf != previous
            assert client.request("/v2/orders", "POST", payload,
                {**headers, "X-CSRF-Token": previous})[0] == 403
            assert client.request("/v2/session/renew", "POST")[0] == 200
        assert len([event for event in shop.audit() if event["kind"] == "commit"]) == 1


def test_reset_during_upload_ends_request_exactly_once(monkeypatch):
    with demo_app_v2() as (shop, url):
        started, ended, calls = threading.Event(), threading.Event(), []
        begin, end = shop.begin, shop.end
        def observed_begin(*args):
            request = begin(*args)
            started.set()
            return request
        def observed_end(*args, **kwargs):
            calls.append(args[0])
            end(*args, **kwargs)
            ended.set()
        monkeypatch.setattr(shop, "begin", observed_begin)
        monkeypatch.setattr(shop, "end", observed_end)
        target = urlsplit(url)
        with socket.create_connection((target.hostname, target.port), timeout=2) as client:
            client.sendall(b"POST /v2/login HTTP/1.1\r\nHost: localhost\r\nContent-Length: 1000\r\n\r\n{")
            assert started.wait(2)
            client.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        assert ended.wait(2)
        assert len(calls) == 1 and shop._pending == 0
        assert Client(url).request("/v2/health")[0] == 200
        audit = shop.audit()
        assert len([event for event in audit if event["kind"] == "start"]) == 2
        assert len([event for event in audit if event["kind"] == "end"]) == 2


def test_dynamic_etag_requires_response_correlation():
    revisions = []
    for seed in (11, 12):
        with demo_app_v2(Profile(dynamic_etag=True, require_etag=True), seed=seed) as (_, url):
            client = Client(url)
            client.login()
            cart = client.cart()
            etag = client.request(f"/v2/carts/{cart}")[2]["ETag"]
            revisions.append(etag)
            assert client.request(f"/v2/carts/{cart}", "PATCH", {"quantity": 2},
                                  {"If-Match": '"1"'})[0] == 412
            assert client.request(f"/v2/carts/{cart}", "PATCH", {"quantity": 2},
                                  {"If-Match": etag})[0] == 200
    assert revisions[0] != revisions[1]


@pytest.mark.parametrize("failed", [False, True])
def test_async_jobs_poll_limits_and_one_use_reservations(failed):
    now = [10.0]
    with demo_app_v2(Profile(job_polls=2, poll_interval=1, job_fails=failed),
                     clock=lambda: now[0]) as (shop, url):
        client = Client(url)
        client.login()
        cart = client.cart()
        status, job, _ = client.request("/v2/reservations", "POST", {"cart_id": cart})
        assert status == 202
        assert client.request(job["status_url"])[1]["status"] == "pending"
        assert client.request(job["status_url"])[0] == 429
        now[0] += 1
        ready = client.request(job["status_url"])[1]
        if failed:
            assert ready["status"] == "failed" and "reservation_id" not in ready
        else:
            body = {"cart_id": cart, "reservation_id": ready["reservation_id"]}
            assert client.request("/v2/orders", "POST", body, {"Idempotency-Key": "one"})[0] == 201
            body["cart_id"] = client.cart()
            assert client.request("/v2/orders", "POST", body, {"Idempotency-Key": "two"})[0] == 409
        assert any(e.get("phase") == ("failed" if failed else "ready") for e in shop.audit())


def test_controlled_rate_faults_and_service_delay_outside_state_lock(monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def delay(_):
        entered.set()
        assert release.wait(2)

    monkeypatch.setattr("p95bench.demo_v2.time.sleep", delay)
    with demo_app_v2(Profile(fail_every=2, delay_seconds=.1, slow_after=1,
                             slow_seconds=.1)) as (shop, url):
        client = Client(url)
        client.login()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(client.request, "/v2/work", "POST")
            try:
                assert entered.wait(2)
                assert client.request("/v2/catalog")[0] == 200
            finally:
                release.set()
            assert future.result()[0] == 200
        assert client.request("/v2/work", "POST")[0] == 429
        assert client.request("/v2/search")[0] == 200
        assert client.request("/v2/browse")[0] == 200
        assert any(e.get("fault") == "rate_limit" for e in shop.audit())
        assert [e["phase"] for e in shop.audit() if e["kind"] == "work"][:2] == ["base", "slow"]


def test_audit_overflow_fails_closed():
    shop = Shop(Profile(), "synthetic")
    with pytest.raises(RuntimeError, match="overflow"):
        for _ in range(32769):
            request = shop.begin("GET", "/v2/catalog")
            shop.end(request, 200)
    assert shop.audit_overflow
    assert shop.dispatch("GET", "/v2/catalog", {}, {}, "synthetic")[0] == 503
    assert len(shop.audit(wait=False)) == 32768
    with pytest.raises(RuntimeError, match="overflow"):
        shop.audit()


@pytest.mark.parametrize("layout", [0, 1])
def test_both_form_layouts_with_hidden_decoys(layout):
    with demo_app_v2(Profile(login_csrf=True, html_layout=layout)) as (_, url):
        client = Client(url)
        form = Form(client.request("/v2/login-form")[1])
        assert form.fields["unused"] == "decoy"
        assert client.login(csrf=form.fields["csrf"])[0] == 200


@pytest.mark.parametrize("redirect", [False, True])
def test_one_use_redirect_and_html_json_challenge_ownership(redirect):
    now = [10.0]
    with demo_app_v2(Profile(require_challenge=True, login_redirect=redirect),
                     clock=lambda: now[0]) as (shop, url):
        client = Client(url, follow_redirects=False)
        status, _, headers = client.login()
        if redirect:
            assert status == 302
            claim_url = headers["Location"]
        else:
            form = Form(client.request("/v2/challenge-form")[1])
            status, _, headers = client.request(form.action, "POST", form.fields)
            assert status == 302
            assert client.request(form.action, "POST", form.fields)[0] == 410
            claim_url = headers["Location"]
        other = Client(url, follow_redirects=False)
        other.login("bench-1")
        assert other.request(claim_url)[0] == 404
        grant = client.request(claim_url)[1]["grant_id"]
        assert client.request(claim_url)[0] == 410
        product = client.request("/v2/catalog")[1]["data"]["products"][0]
        body = {"product_id": product["id"], "variant_id": product["variants"][0]["id"],
                "grant_id": grant}
        assert client.request("/v2/carts", "POST", body)[0] == 201
        assert client.request("/v2/carts", "POST", body)[0] == 403
        form = Form(client.request("/v2/challenge-form")[1])
        now[0] += 3
        assert client.request(form.action, "POST", form.fields)[0] == 410
        assert any(e.get("grant") == grant for e in shop.audit() if e["kind"] == "cart")


def test_scheduled_etag_conflict_changes_only_external_revision():
    with demo_app_v2(Profile(etag_conflict_once=True)) as (shop, url):
        client = Client(url)
        client.login()
        cart = client.cart()
        path = f"/v2/carts/{cart}"
        assert client.request(path, "PATCH", {"quantity": 2}, {"If-Match": '"1"'})[0] == 412
        etag = client.request(path)[2]["ETag"]
        assert etag == '"2"'
        assert client.request(path, "PATCH", {"quantity": 2}, {"If-Match": etag})[0] == 200
        assert len([e for e in shop.audit() if e["kind"] == "cart_update"]) == 1


def test_last_page_eligibility_and_checkout_delay_profile():
    with demo_app_v2(Profile(eligible_index=0, reverse_catalog=True,
                             checkout_delay_seconds=.01)) as (shop, url):
        client = Client(url)
        assert client.request("/v2/health")[0] == 200
        client.login()
        page, pages = "/v2/catalog/page", 0
        while page:
            data = client.request(page)[1]
            product = data["data"]["products"][0]
            pages += 1
            if product["variants"][0]["in_stock"]:
                break
            page = data["next"]
        assert pages == 3
        body = {"product_id": product["id"], "variant_id": product["variants"][0]["id"]}
        cart = client.request("/v2/carts", "POST", body)[1]["id"]
        assert client.request("/v2/orders", "POST", {"cart_id": cart},
                              {"Idempotency-Key": "one"})[0] == 201
        events = shop.audit()
        request = next(e["request"] for e in events if e["kind"] == "commit")
        start = next(e["time"] for e in events if e["request"] == request and e["kind"] == "start")
        commit = next(e["time"] for e in events if e["request"] == request and e["kind"] == "commit")
        assert commit - start >= .01


def test_time_based_phase_boundary_uses_owner_clock():
    now = [10.0]
    with demo_app_v2(Profile(slow_after_seconds=5, slow_seconds=.01),
                     clock=lambda: now[0]) as (shop, url):
        client = Client(url)
        client.login()
        assert client.request("/v2/work", "POST")[0] == 200
        now[0] += 5
        assert client.request("/v2/work", "POST")[0] == 200
        assert [e["phase"] for e in shop.audit() if e["kind"] == "work"] == ["base", "slow"]


@pytest.mark.parametrize("payload", [b"", b"[]", b"{", b'{"cart_id": []}'])
def test_empty_malformed_and_nested_bodies(payload):
    with demo_app_v2() as (shop, url):
        request = Request(url + "/v2/login", data=payload, method="POST")
        with pytest.raises(HTTPError) as error:
            build_opener().open(request, timeout=3)
        assert error.value.code == (401 if not payload else 400)
        assert shop.audit()[-1]["kind"] == "end"


@pytest.mark.parametrize("kwargs", [{"expire_after": -1}, {"delay_seconds": float("nan")},
                                   {"delay_seconds": 3}, {"job_polls": True},
                                   {"rotate_csrf": "yes"}])
def test_profile_rejects_unbounded_or_invalid_settings(kwargs):
    with pytest.raises(ValueError):
        Profile(**kwargs)


def test_v1_demo_has_not_gained_v2_routes():
    with demo_app() as (url, _):
        client = Client(url)
        assert client.request("/login", "POST", {"username": "bench", "password": "bench"})[0] == 200
        assert client.request("/v2/catalog")[0] == 404
        assert client.request("/products")[1] == {"products": [{"id": "sku-1", "price": 10}]}
