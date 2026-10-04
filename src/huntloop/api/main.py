"""
FastAPI backend service for HuntLoop.

A separate service from the scraper/orchestrator (main.py) and the
cron/observability stack - its own `api` container in
docker-compose.yml, its own port, connected directly to the real host
Postgres (not the docker-compose `db` service - see docker-compose.yml's
comment on the `api` service for why). Doesn't touch or get touched by
any of those.

Run locally via (with `src` on PYTHONPATH, same as any other huntloop
module - see README.md):

    uvicorn huntloop.api.main:app --reload

Auto-generated interactive docs are then at http://localhost:8000/docs.

CORS: browser-based clients (the frontend/ Next.js app) need
Access-Control-Allow-Origin - without CORSMiddleware, the browser's
preflight OPTIONS request 405s before the real request is even attempted
(curl/server-to-server calls are unaffected, since CORS is a
browser-enforced restriction, not a server-side one - confirmed this was
the actual failure via the browser's network panel, not assumed, when
frontend/ first tried to call this API). CORS_ALLOWED_ORIGINS
(comma-separated) lets this be overridden per environment; defaults to
just the Next.js dev server's own default origin.

Client-IP resolution behind the optional Caddy proxy (see
docker-compose.yml's "proxy" profile / caddy/Caddyfile): a request
reaching `api` through Caddy arrives from Caddy's own container, not the
real visitor - request.client.host (what huntloop.api.routers.drafting's
and .resumes' per-IP rate limiters key on) would otherwise be Caddy's
address for EVERY proxied request, collapsing every real visitor into
one shared rate-limit bucket (confirmed in the security audit - every
proxied request landed as the same source). Caddy's reverse_proxy sets
X-Forwarded-For with the real client IP by default (confirmed against
caddy/Caddyfile and Caddy's own documented reverse_proxy behavior - no
Caddyfile change needed for this), so the fix is having `api` trust that
header FROM CADDY SPECIFICALLY and rewrite request.client accordingly -
uvicorn's own ProxyHeadersMiddleware does exactly this.

TRUSTED_PROXY_IPS (comma-separated, defaults to just "127.0.0.1") names
the ONLY peer address(es) this will accept a forwarded-client-IP header
from - deliberately not "*"/trust-everyone, which would let any request
that ever reaches this service by any other path (e.g. if it were
somehow exposed another way) spoof its own source IP outright.
docker-compose.yml sets this to the `caddy` service's own pinned
internal container address (see its `default` network entry there) when
the "proxy" profile is used; unset/default elsewhere. A request that
does NOT come from a trusted peer has every one of these headers
ignored entirely - request.client.host stays the real TCP peer address,
exactly as before, which is what keeps plain/local/non-proxied access
(local dev, `docker compose up` without --profile proxy) resolving
correctly with no behavior change from before this was added.

Render needs a second, different mechanism on top of the above - see
huntloop.api.trusted_client_ip's own docstring for the full reasoning.
Short version: on Render this process always sees request peer
127.0.0.1 (matching the TRUSTED_PROXY_IPS default), but Render sits
behind Cloudflare and does not strip a client-supplied X-Forwarded-For
header, only append to it - so trusting X-Forwarded-For alone there
would let a visitor spoof their own rate-limit identity. Cloudflare's
own True-Client-IP/CF-Connecting-IP headers are not client-spoofable,
so TrustedClientIPMiddleware prefers those (only from a trusted peer)
and otherwise falls through to ProxyHeadersMiddleware's X-Forwarded-For
handling unchanged - that keeps the Caddy path (which never sends
either of those two headers) working exactly as before.

DEMO_MODE (see huntloop.demo_mode): off by default, and off is byte for
byte the same app this module always built before DEMO_MODE existed -
every change below is gated on is_demo_mode() being true. When on:
  - huntloop.api.routers.drafting (the BYOK LLM route) and
    huntloop.api.routers.resumes.unsafe_router (resume upload and
    activation) are simply never included, so those routes do not
    exist rather than existing and refusing requests.
  - huntloop.api.routers.demo_info is mounted (GET /demo-info), giving
    the frontend banner a snapshot date to show.
  - every route gets a generous, configurable per-IP rate limit
    (DEMO_RATE_LIMIT_MAX_REQUESTS / DEMO_RATE_LIMIT_WINDOW_SECONDS),
    applied once as an app-level dependency and reusing the same
    _RateLimiter class the drafting/resumes routes already use rather
    than a new implementation.
create_app() is a plain factory so tests can build a fresh demo-mode
app (with its own rate limiter) without disturbing the module-level
`app` instance every other test already depends on.
"""
import os

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from huntloop.api.routers import admin, admin_feedback, dashboard, demo_info, drafting, feedback, health, jobs, resumes
from huntloop.api.routers.drafting import _RateLimiter
from huntloop.api.routers.resumes import MAX_UPLOAD_BYTES, MaxUploadSizeMiddleware
from huntloop.api.trusted_client_ip import TrustedClientIPMiddleware
from huntloop.demo_mode import demo_rate_limit_max_requests, demo_rate_limit_window_seconds, is_demo_mode


def _demo_rate_limit_dependency():
    """Builds one fresh _RateLimiter per app instance (so a test building
    several demo apps never shares rate-limit state between them) and
    returns a FastAPI dependency function that enforces it, keyed on the
    caller's IP the same way drafting.py/resumes.py's own limiters are."""
    limiter = _RateLimiter(demo_rate_limit_max_requests(), demo_rate_limit_window_seconds())

    def _check(request: Request) -> None:
        client_ip = request.client.host if request.client else "unknown"
        if not limiter.allow(client_ip):
            raise HTTPException(
                429,
                f"Rate limit exceeded for the demo API ({limiter.max_requests} requests per "
                f"{int(limiter.window_seconds)}s per client). Try again shortly.",
            )

    _check.limiter = limiter
    return _check


def create_app() -> FastAPI:
    demo_mode = is_demo_mode()

    dependencies = [Depends(_demo_rate_limit_dependency())] if demo_mode else []
    app = FastAPI(title="HuntLoop API", dependencies=dependencies)

    _default_origins = "http://localhost:3000,http://127.0.0.1:3000"
    _allowed_origins = os.getenv("CORS_ALLOWED_ORIGINS", _default_origins).split(",")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_allowed_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # See this module's own docstring for the full reasoning. Scoped to a
    # specific trusted peer (or peers), never "*" - see TRUSTED_PROXY_IPS
    # above.
    _trusted_proxy_ips = [ip.strip() for ip in os.getenv("TRUSTED_PROXY_IPS", "127.0.0.1").split(",") if ip.strip()]
    app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=_trusted_proxy_ips)
    # Added after ProxyHeadersMiddleware so it becomes the outermost layer
    # and runs first on the way in - see this module's docstring and
    # huntloop.api.trusted_client_ip's for why Render needs this ahead of
    # plain X-Forwarded-For trust.
    app.add_middleware(TrustedClientIPMiddleware, trusted_hosts=_trusted_proxy_ips)

    app.include_router(health.router)
    app.include_router(jobs.router)
    app.include_router(dashboard.router)
    app.include_router(resumes.router)
    # Public in both modes - no API key/BYOK involved, so it's not
    # subject to DEMO_MODE's drafting/resume-upload gating below.
    app.include_router(feedback.router)
    # Admin feedback review (GET/PATCH /admin/feedback*) - mounted
    # unconditionally in both demo and non-demo mode, same as
    # feedback.router above. Unlike that router, every route here is
    # gated behind huntloop.api.admin_auth.require_admin (a verified,
    # allow-listed Clerk session) regardless of DEMO_MODE - there is no
    # separate demo-mode carve-out for this router, since it's the site
    # owner's own review tool, not a visitor-facing feature.
    app.include_router(admin_feedback.router)
    # GET /admin/whoami - same unconditional-in-both-modes, require_admin-gated
    # treatment as admin_feedback.router above. Used only by the frontend
    # nav to decide whether to render a link to /admin/feedback at all.
    app.include_router(admin.router)

    if demo_mode:
        app.include_router(demo_info.router)
    else:
        # See MaxUploadSizeMiddleware's own docstring
        # (huntloop.api.routers.resumes) for why this has to be ASGI-layer
        # middleware rather than a check inside upload_resume() itself.
        # Skipped in demo mode since the route it guards is never mounted.
        app.add_middleware(MaxUploadSizeMiddleware, path="/resumes/upload", max_bytes=MAX_UPLOAD_BYTES)
        app.include_router(resumes.unsafe_router)
        app.include_router(drafting.router)

    return app


app = create_app()
