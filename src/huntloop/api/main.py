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
"""
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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

# See MaxUploadSizeMiddleware's own docstring (huntloop.api.routers.resumes)
# for why this has to be ASGI-layer middleware rather than a check inside
# upload_resume() itself.
app.add_middleware(MaxUploadSizeMiddleware, path="/resumes/upload", max_bytes=MAX_UPLOAD_BYTES)

app.include_router(health.router)
app.include_router(jobs.router)
app.include_router(dashboard.router)
app.include_router(resumes.router)
app.include_router(drafting.router)
