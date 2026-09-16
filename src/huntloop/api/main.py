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
the ONLY peer address(es) this will accept an X-Forwarded-For header
from - deliberately not "*"/trust-everyone, which would let any request
that ever reaches this service by any other path (e.g. if it were
somehow exposed another way) spoof its own source IP outright.
docker-compose.yml sets this to the `caddy` service's own pinned
internal container address (see its `default` network entry there) when
the "proxy" profile is used; unset/default elsewhere. A request that
does NOT come from a trusted peer has this header ignored entirely -
request.client.host stays the real TCP peer address, exactly as before,
which is what keeps plain/local/non-proxied access (local dev, `docker
compose up` without --profile proxy) resolving correctly with no
behavior change from before this was added.
"""
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from huntloop.api.routers import dashboard, drafting, health, jobs, resumes
from huntloop.api.routers.resumes import MAX_UPLOAD_BYTES, MaxUploadSizeMiddleware

app = FastAPI(title="HuntLoop API")

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

# See MaxUploadSizeMiddleware's own docstring (huntloop.api.routers.resumes)
# for why this has to be ASGI-layer middleware rather than a check inside
# upload_resume() itself.
app.add_middleware(MaxUploadSizeMiddleware, path="/resumes/upload", max_bytes=MAX_UPLOAD_BYTES)

app.include_router(health.router)
app.include_router(jobs.router)
app.include_router(dashboard.router)
app.include_router(resumes.router)
app.include_router(drafting.router)
