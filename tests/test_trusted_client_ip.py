"""
Unit tests for huntloop.api.trusted_client_ip.TrustedClientIPMiddleware -
the Render-specific client-IP resolution described in that module's and
huntloop.api.main's docstrings. Exercises the middleware directly against
a tiny app (not the full huntloop app) so these tests only depend on the
one thing under test: does scope["client"] end up right given a peer and
a set of headers.
"""
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from huntloop.api.trusted_client_ip import TrustedClientIPMiddleware


def _echo_client(request: Request) -> JSONResponse:
    return JSONResponse({"client": request.client.host if request.client else None})


def _build_app(trusted_hosts: list[str]) -> Starlette:
    app = Starlette(routes=[Route("/", _echo_client)])
    app.add_middleware(TrustedClientIPMiddleware, trusted_hosts=trusted_hosts)
    return app


def test_true_client_ip_used_when_peer_trusted():
    app = _build_app(["127.0.0.1"])
    client = TestClient(app, client=("127.0.0.1", 12345))

    resp = client.get("/", headers={"True-Client-IP": "203.0.113.5"})
    assert resp.json()["client"] == "203.0.113.5"


def test_cf_connecting_ip_used_as_fallback():
    app = _build_app(["127.0.0.1"])
    client = TestClient(app, client=("127.0.0.1", 12345))

    resp = client.get("/", headers={"CF-Connecting-IP": "203.0.113.9"})
    assert resp.json()["client"] == "203.0.113.9"


def test_true_client_ip_preferred_over_cf_connecting_ip():
    app = _build_app(["127.0.0.1"])
    client = TestClient(app, client=("127.0.0.1", 12345))

    resp = client.get(
        "/",
        headers={"True-Client-IP": "203.0.113.5", "CF-Connecting-IP": "198.51.100.1"},
    )
    assert resp.json()["client"] == "203.0.113.5"


def test_two_distinct_visitors_behind_trusted_proxy_get_distinct_ips():
    """The real property this middleware exists for: two different real
    visitors, both arriving through the same trusted Render peer, must
    resolve to two different client identities - not collapse into one
    shared rate-limit bucket."""
    app = _build_app(["127.0.0.1"])
    client = TestClient(app, client=("127.0.0.1", 12345))

    first = client.get("/", headers={"True-Client-IP": "203.0.113.5"})
    second = client.get("/", headers={"True-Client-IP": "198.51.100.42"})

    assert first.json()["client"] == "203.0.113.5"
    assert second.json()["client"] == "198.51.100.42"
    assert first.json()["client"] != second.json()["client"]


def test_header_ignored_when_peer_not_trusted():
    """A request whose immediate peer is NOT a trusted proxy must never
    have its reported client IP overridden by a header it could have
    set itself - otherwise anyone reaching the process directly could
    spoof an arbitrary True-Client-IP/CF-Connecting-IP and impersonate
    any other visitor."""
    app = _build_app(["127.0.0.1"])
    client = TestClient(app, client=("203.0.113.66", 12345))

    resp = client.get("/", headers={"True-Client-IP": "9.9.9.9"})
    assert resp.json()["client"] == "203.0.113.66"


def test_no_header_present_leaves_peer_as_client():
    """The Caddy path: no True-Client-IP/CF-Connecting-IP header is ever
    sent, so this middleware must do nothing and leave scope["client"]
    for ProxyHeadersMiddleware's existing X-Forwarded-For handling."""
    app = _build_app(["127.0.0.1"])
    client = TestClient(app, client=("127.0.0.1", 12345))

    resp = client.get("/")
    assert resp.json()["client"] == "127.0.0.1"


def test_empty_header_value_is_ignored():
    app = _build_app(["127.0.0.1"])
    client = TestClient(app, client=("127.0.0.1", 12345))

    resp = client.get("/", headers={"True-Client-IP": "   "})
    assert resp.json()["client"] == "127.0.0.1"
