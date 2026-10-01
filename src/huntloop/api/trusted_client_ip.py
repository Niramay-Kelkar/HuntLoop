"""
Resolves the real visitor IP when `api` runs behind Render's edge, for
the per-IP rate limiters in huntloop.api.routers.drafting/resumes and
huntloop.api.main's own demo-mode limiter - all of which key on
request.client.host.

Why this exists alongside uvicorn's ProxyHeadersMiddleware (already
wired in huntloop.api.main for the Caddy case): ProxyHeadersMiddleware
trusts X-Forwarded-For once the immediate TCP peer is in
TRUSTED_PROXY_IPS. That is correct for Caddy, which is the only hop and
sets X-Forwarded-For to exactly the real client IP with nothing else in
it. It is NOT correct for Render: Render's own edge proxy connects to
this container over loopback (confirmed via Render's own docs/support
threads - the app always sees request peer 127.0.0.1 there, which is
also this project's TRUSTED_PROXY_IPS default), but Render sits behind
Cloudflare and does not strip a client-supplied X-Forwarded-For header -
it only appends its own hop to whatever the client already sent. A
visitor can therefore prepend an arbitrary fake IP to X-Forwarded-For
and, since the peer (127.0.0.1) is trusted, ProxyHeadersMiddleware would
report that fake IP as request.client.host, letting one visitor spin up
unlimited rate-limit buckets.

Cloudflare, which terminates the actual public connection in front of
Render, sets True-Client-IP (and CF-Connecting-IP) itself from the real
TCP connection it sees, overwriting any client-supplied value of the
same name - these two headers cannot be spoofed by the client the way
X-Forwarded-For can. See Render's own docs/community guidance on this
distinction.

TrustedClientIPMiddleware: when the request's current scope["client"]
host is in `trusted_hosts` AND one of these headers is present, rewrite
scope["client"] to that header's IP and leave the port as the Render/
Caddy-dummy value, same as ProxyHeadersMiddleware already does. Added to
the app AFTER ProxyHeadersMiddleware (so it runs first, as the
outermost layer on the way in - see huntloop.api.main's add_middleware
ordering comment) so:
  - On Render (header present, peer trusted): this middleware sets the
    real client IP, and ProxyHeadersMiddleware then sees a now-untrusted
    scope["client"] and leaves it alone, so it never gets a chance to
    trust a spoofed X-Forwarded-For instead.
  - Locally/behind Caddy (no True-Client-IP/CF-Connecting-IP header,
    since Caddy never sets either): this middleware does nothing,
    scope["client"] is still the trusted Caddy/loopback peer, and
    ProxyHeadersMiddleware's existing X-Forwarded-For handling runs
    exactly as before - zero behavior change for that path.
  - From an untrusted peer (neither Render nor Caddy, e.g. someone
    reaching the process directly): scope["client"] host is not in
    trusted_hosts, so both of these headers AND X-Forwarded-For are
    ignored and the real TCP peer is used, same as always.
"""
from starlette.types import ASGIApp, Receive, Scope, Send

_TRUSTED_IP_HEADERS = (b"true-client-ip", b"cf-connecting-ip")


class TrustedClientIPMiddleware:
    def __init__(self, app: ASGIApp, trusted_hosts: list[str]) -> None:
        self.app = app
        self._trusted_hosts = set(trusted_hosts)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            client = scope.get("client")
            client_host = client[0] if client else None

            if client_host in self._trusted_hosts:
                headers = dict(scope["headers"])
                for header_name in _TRUSTED_IP_HEADERS:
                    if header_name in headers:
                        real_ip = headers[header_name].decode("latin1").strip()
                        if real_ip:
                            port = client[1] if client else 0
                            scope["client"] = (real_ip, port)
                        break

        await self.app(scope, receive, send)
