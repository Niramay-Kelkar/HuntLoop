# Deploying the public demo

Step by step instructions for putting the read-only demo (see the
"Demo mode" section of README.md and `huntloop.demo_mode`) on free
hosting: Neon for the database, Render for the API, Vercel for the
frontend. No real values are filled in here - follow along in your own
accounts.

Nothing is deployed as part of writing this document. You do the
clicking.

## Before you start

- A GitHub account with this repo pushed to it (Render and Vercel both
  import from a Git remote).
- A Neon account (neon.tech).
- A Render account (render.com).
- A Vercel account (vercel.com).
- Docker installed locally, for the rehearsal steps and for running
  `scripts/deploy_demo_data.py` (it needs the `app` image for its
  torch/sentence-transformers dependency - see that script's own
  docstring).

## 1. Create the Neon project (the database)

1. In the Neon console, create a new project.
2. Pick a **region close to where you will deploy the Render service**
   (see the render.yaml note below on confirming Render's own region
   once you've picked one) - this keeps API-to-database latency low.
   Which exact regions Render and Neon both offer, and which pairing is
   closest, is not verified in this document - check both consoles.
3. Once the project is created, open the Neon console's **Extensions**
   page (or run `CREATE EXTENSION IF NOT EXISTS vector;` yourself) and
   confirm **pgvector** is available/enabled for the project. HuntLoop's
   own Alembic migration `c2d25907fe8e` also runs `CREATE EXTENSION IF
   NOT EXISTS vector;` as part of `alembic upgrade head` (step 3 below
   does this for you), so this is a belt-and-suspenders check, not
   strictly required first - but confirm it either way, since a role
   without CREATE privilege on extensions would otherwise fail silently
   late in step 3.
4. Neon gives you (at least) two connection strings:
   - The **pooled** connection string (usually the default one shown,
     often with `-pooler` in the hostname) - use this one for
     `DATABASE_URL` on Render, i.e. what the running API actually
     queries against.
   - The **direct** (unpooled) connection string - use this one for
     `SOURCE_DATABASE_URL`/`TARGET_DATABASE_URL` when running
     `scripts/deploy_demo_data.py` from your own terminal (migrations
     and the bulk copy are long-lived operations that behave more
     predictably on a direct connection than through a transaction
     pooler).
5. Both connection strings already include `sslmode=require` (Neon
   requires SSL) - don't strip it. HuntLoop's `DATABASE_URL` shape is
   `postgresql+psycopg2://...`. Neon gives you a plain `postgresql://...`
   string, so prefix it with `+psycopg2` after `postgresql`, keeping
   everything else (including the `?sslmode=require` query string)
   unchanged.

## 2. Load the demo snapshot

Run this from your own terminal, against your own real local production
Postgres as the source and the new Neon project as the target. Set the
two variables only in your own shell - never put them in a file that
gets committed.

Keep the quotes. A Neon connection string contains `&` and `?`, so an
unquoted value is split by the shell - which breaks the assignment and
can echo the whole line, password included, through bash's job-control
output. For the same reason, never `source` / dot-run an env file that
holds these values, and never `set -a` or `export $(cat ...)` one; if
you keep them in a file, read it with `python-dotenv`'s
`dotenv_values()` and pass the values into the child process's
environment. See CLAUDE.md's "Secret handling" section.

```bash
export SOURCE_DATABASE_URL="postgresql+psycopg2://<user>:<password>@localhost:5432/jobsight"
export TARGET_DATABASE_URL="postgresql+psycopg2://<user>:<password>@<your-neon-host>/<dbname>?sslmode=require"

docker compose run --rm \
  -e SOURCE_DATABASE_URL -e TARGET_DATABASE_URL \
  app python scripts/deploy_demo_data.py
```

This one command:

1. Refuses outright if `TARGET_DATABASE_URL` resolves to the same
   host/port/database as `SOURCE_DATABASE_URL`, or to
   `localhost:5432` (this project's real local production database -
   see CLAUDE.md). If either refusal fires, fix the variable it names
   and rerun.
2. Runs `alembic upgrade head` against the target - creates the full
   schema and enables the `vector` extension.
3. Runs `scripts/build_demo_dataset.py --force` - copies a small, safe
   sample (essentially every company with a relevant, already-embedded
   posting, each capped at 40 postings, their companies/sources/
   locations, sponsor aggregates for resolved companies, one fictional
   resume) from the source into the target. `--force` means this is safe
   to rerun any time you want a fresh snapshot - it clears the target's
   demo-relevant tables first, every time, rather than skipping because
   the target already has data.
4. Prints row counts per table and the target database's total size.

This needs the `app` Docker image (built from the repo root's
`Dockerfile`, not `Dockerfile.demo`) because building the demo resume's
embedding needs torch/sentence-transformers, which the lean
`Dockerfile.demo` image deliberately does not include. Build it first if
you haven't: `docker compose build app`.

To refresh the snapshot later (new scraped data, a different sample),
just rerun the same command.

## 3. Deploy the Render Blueprint (the API)

1. Push this branch's `render.yaml` to the repo (already done once this
   branch merges to `master` - Render Blueprints read from a branch you
   point it at, so you can also point Render at this branch directly to
   deploy before merging).
2. In the Render dashboard, **New > Blueprint**, pick this repo, and
   point it at the branch containing `render.yaml`.
3. Render parses `render.yaml` and shows one web service,
   `huntloop-demo-api`, built from `Dockerfile.demo`, free plan, health
   check at `/health`.
4. Render will prompt for the environment variables marked `sync: false`
   in `render.yaml` - fill these in on the dashboard, never in the repo:
   - `DATABASE_URL` - the Neon **pooled** connection string from step 1,
     with `postgresql+psycopg2://` as the scheme and `?sslmode=require`
     kept.
   - `CORS_ALLOWED_ORIGINS` - leave this as a placeholder for now (e.g.
     `http://localhost:3000`), then come back to set it to your real
     Vercel URL once step 4 gives you one (see "Wire CORS" below). Render
     lets you edit env vars after the first deploy without needing to
     re-run the Blueprint.
   - `CLERK_SECRET_KEY` - from your Clerk app's dashboard (Configure ->
     API Keys). This must be the same Clerk application whose
     publishable key you set on Vercel in step 4 below - Clerk issues
     one secret key per application, shared by both the frontend's own
     Next.js proxy and this API.
   - `ADMIN_ALLOWED_EMAILS` - comma-separated Clerk account email(s)
     allowed to reach `/admin/feedback` (huntloop.api.admin_auth). A
     valid Clerk login alone is not enough - anyone can sign up for a
     Clerk account on the public demo, so this allowlist is what
     actually gates admin access.
5. Every other env var (`DEMO_MODE`, `DEMO_RATE_LIMIT_MAX_REQUESTS`,
   `DEMO_RATE_LIMIT_WINDOW_SECONDS`, `TRUSTED_PROXY_IPS`) already has a
   fixed value in `render.yaml` - nothing else to fill in. (There is
   deliberately no `CLERK_AUTHORIZED_PARTIES` var - see "Wire CORS"
   below for why `CORS_ALLOWED_ORIGINS` alone already covers it.)
6. Deploy. Watch the build logs, then once it's live, hit
   `https://<your-service>.onrender.com/health` and confirm it returns
   `{"status": "ok"}`.

**Fields `render.yaml` could not verify offline** (no Render account was
used while writing it - confirm these yourself, see render.yaml's own
comment block too):

- The exact free-plan identifier (`plan: free`) and whether a Docker
  runtime web service is still available on it.
- Which region the service lands in by default, and whether you should
  pin one explicitly (`region:`) to sit near your Neon project.
- Render's current cold-start/spin-down behavior on the free plan (see
  "What cold starts will look like" below) - this document's description
  of it is general free-tier PaaS behavior, not something confirmed
  against this specific deploy.

### Why `TRUSTED_PROXY_IPS`/client-IP resolution needs no further setup here

Render's own edge proxy connects to this container over loopback, so the
app always sees request peer `127.0.0.1` - the same value
`TRUSTED_PROXY_IPS` already defaults to. Render sits behind Cloudflare,
which sets a `True-Client-IP` header from the real visitor connection
and does not let a client override it (unlike `X-Forwarded-For`, which
Render appends to but does not sanitize - a visitor could otherwise
spoof their own rate-limit identity). `TrustedClientIPMiddleware`
(`src/huntloop/api/trusted_client_ip.py`, wired into
`huntloop.api.main.create_app()`) prefers `True-Client-IP`/
`CF-Connecting-IP` over `X-Forwarded-For` whenever the connecting peer is
trusted, so the demo's per-IP rate limit sees real, distinct visitor IPs
on Render without any extra configuration. Nothing to change here beyond
what `render.yaml` already sets.

## 4. Deploy to Vercel (the frontend)

1. In the Vercel dashboard, **Add New > Project**, import this repo.
2. **Root Directory**: set to `frontend` - this repo is not a Next.js
   app at its root.
3. Framework preset: Next.js (Vercel should detect this automatically
   once the root directory is set).
4. **Build-time environment variables** (Vercel calls these "Environment
   Variables" and bakes `NEXT_PUBLIC_`-prefixed ones into the browser
   bundle at build time, same as the project's own Docker build - see
   README.md's "Frontend" section):
   - `NEXT_PUBLIC_API_URL` = your Render service's URL from step 3
     (`https://<your-service>.onrender.com`), no trailing slash.
   - `NEXT_PUBLIC_DEMO_MODE` = `true`.
   - `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` = from the same Clerk app
     whose secret key you set on Render in step 3 (Clerk dashboard ->
     Configure -> API Keys).
   - `CLERK_SECRET_KEY` - yes, also here, not just on Render: the
     frontend's own proxy (`frontend/src/proxy.ts`) verifies the Clerk
     session itself (to redirect a signed-out visitor to sign-in before
     `/admin/feedback` ever renders) and needs this server-side secret
     to do so, separately from this API's own verification of the same
     token. Set as a plain (non-`NEXT_PUBLIC_`) environment variable -
     never prefix it with `NEXT_PUBLIC_`, which would bake a real
     secret into the public browser bundle.
5. Deploy. Vercel gives you a URL
   (`https://<your-project>.vercel.app` or similar).

## 5. Wire CORS

Go back to the Render dashboard, open `huntloop-demo-api`'s environment
variables, and set `CORS_ALLOWED_ORIGINS` to your real Vercel URL from
step 4 (exact origin, e.g. `https://your-project.vercel.app`, no
trailing slash, comma-separated if you also want to allow a custom
domain). Save - Render redeploys the service with the new value.

### Why this also fixes Clerk admin logins, not just CORS

`CORS_ALLOWED_ORIGINS` isn't only a CORS setting here.
`huntloop.api.admin_auth._authorized_parties()` (the Clerk SDK's own
replay-protection check - it rejects a session token whose `azp` claim
isn't a trusted origin) falls back to this same `CORS_ALLOWED_ORIGINS`
value whenever `CLERK_AUTHORIZED_PARTIES` itself is unset, which it
deliberately is in `render.yaml` (see that file's own comment). **This
step is therefore required for `/admin/feedback` to work at all, not
just for the frontend to be able to call the API** - skipping it, or
setting `CORS_ALLOWED_ORIGINS` to the wrong origin, makes every admin
session fail Clerk's authorized-party check with a 401, regardless of
whether the signed-in email is on `ADMIN_ALLOWED_EMAILS` (this is
exactly what happened in the 2026-10 incident that added this
paragraph - see SESSIONS.md "Admin feedback page failed to load on the
live demo"). If you ever need the Clerk-trusted origin to genuinely
differ from the CORS origin, set `CLERK_AUTHORIZED_PARTIES` explicitly
on Render to override this fallback.

## 6. Run the smoke test against the live URLs

```bash
API_BASE_URL="https://<your-service>.onrender.com" \
FRONTEND_URL="https://<your-project>.vercel.app" \
python3 scripts/smoke_test_demo.py
```

This checks health, demo-info, the jobs list (with scores), one job
detail (sponsor data shape), dashboard stats, that the three BYOK/write
routes return 404, that the application-status PATCH is a no-op, and
that the CORS header comes back for your frontend's exact origin. It
prints per-check timing (including the first request, which is the one
to watch for a cold start - see below) and exits non-zero if anything
fails.

## What cold starts will look like

Render's free plan spins a service down after a period of no traffic and
spins it back up on the next request - the first request after a quiet
period will be slow (render.yaml's own comment flags this as unverified
against this specific deploy, so expect something on the order of tens of
seconds, not milliseconds). `scripts/smoke_test_demo.py`'s first check
(`GET /health`) is deliberately the one most likely to catch this, and
its printed timing will show it plainly. The frontend's
`NEXT_PUBLIC_DEMO_MODE` build already shows a "waking up" message if the
backend is slow to answer (`DemoWakeUpGate`, see
`frontend/src/components/DemoWakeUpGate.tsx`) - nothing further to wire
up for this.

## Refreshing the snapshot

Rerun step 2's command whenever you want the demo to show fresher data.
It is safe to rerun - `scripts/deploy_demo_data.py` always rebuilds the
target's demo-relevant tables from scratch rather than skipping because
they're already populated. Nothing on Render or Vercel needs to change.
The running API just starts returning the refreshed rows on its next
query.

## Taking the demo down

- **Render**: suspend or delete the `huntloop-demo-api` service from
  its dashboard. Suspending keeps the configuration (env vars,
  `render.yaml` wiring) so it's cheap to bring back later, while deleting
  removes it entirely.
- **Vercel**: delete the project, or just stop pointing a domain at it -
  an idle Vercel static/SSR deployment on the free plan does not need
  separate teardown to avoid cost.
- **Neon**: delete the project (or just the demo database/branch within
  it) once you're done - this also permanently removes the demo
  snapshot, which is fine, since step 2 can always rebuild it from
  production again later.

Nothing about taking the demo down touches production - `SOURCE_DATABASE_URL`
in step 2 only ever reads from your real local Postgres read-only, and
every refusal check in `scripts/deploy_demo_data.py` exists specifically
so this pipeline can never write back to it.

## Troubleshooting

- **`scripts/deploy_demo_data.py` refuses with "both resolve to
  (...)"**: `SOURCE_DATABASE_URL` and `TARGET_DATABASE_URL` point at the
  same host/port/database. Double check you copied the Neon connection
  string into `TARGET_DATABASE_URL`, not a second copy of your local
  one.
- **`scripts/deploy_demo_data.py` refuses with "this is the local
  production Postgres convention..."**: `TARGET_DATABASE_URL` resolves
  to `localhost:5432`. This script must never run against that database
  - fix the variable to point at Neon.
- **`alembic upgrade head` fails with a permission error creating the
  `vector` extension**: the Neon role in your connection string lacks
  CREATE privilege on extensions. Enable pgvector from the Neon
  console's Extensions page directly (see step 1.3), then rerun.
- **Render health check fails / service never goes live**: check the
  build logs first (a missing/misspelled env var will usually show up
  as the app failing to start, since `DATABASE_URL` is required at
  import time - see `huntloop.settings`). Confirm `DATABASE_URL` on
  Render is the full Neon pooled connection string including
  `?sslmode=require`.
- **Frontend loads but shows no jobs / network errors in the browser
  console**: almost always a CORS or URL mismatch - confirm
  `NEXT_PUBLIC_API_URL` on Vercel exactly matches the Render service URL
  (no trailing slash), and that `CORS_ALLOWED_ORIGINS` on Render exactly
  matches the Vercel URL (no trailing slash, right scheme).
  `NEXT_PUBLIC_API_URL` is baked in at Vercel build time - changing it
  requires a redeploy, not just an env var edit.
- **`scripts/smoke_test_demo.py`'s CORS check fails**: confirm you
  passed the exact Vercel origin as `FRONTEND_URL` (scheme + host, no
  path, no trailing slash) and that it matches `CORS_ALLOWED_ORIGINS` on
  Render exactly - `CORSMiddleware` requires an exact origin match, not a
  prefix or wildcard.
- **One visitor's requests seem to be rate-limited by another visitor's
  traffic**: see "Why `TRUSTED_PROXY_IPS`/client-IP resolution needs no
  further setup here" above - this should not happen given the current
  wiring. If it does, confirm the service is actually being reached
  through Render's own URL (not some other path that bypasses Render's
  edge) and that `TRUSTED_PROXY_IPS` on Render is still `127.0.0.1`.

---

## Resume Deep-Dive: the manual setup it needs

The Resume Deep-Dive (`POST /jobs/{id}/deep-dive`, `GET
/deep-dives/{id}` — `huntloop.api.routers.deep_dive`) runs its LLM
analysis inside a Modal function and is polled by the frontend. Three
one-time manual steps are needed before it works on a deployment;
without them the endpoint returns a clean, structured `503` with
`"code": "modal_unavailable"` and the frontend panel says so, so nothing
is broken in the meantime — the feature is simply unavailable.

**1. Create the `huntloop-llm` Modal Secret.** This is where the LLM
provider keys live, and the reason the whole feature is shaped this way:
the demo API itself has no provider keys in its environment at all (see
`render.yaml`), so no key ever reaches Render. The secret must be named
exactly `huntloop-llm` and contain:

| Variable | Required | Notes |
|---|---|---|
| `GROQ_API_KEY` | yes | Both Groq stages (`gpt-oss-120b`, then `gpt-oss-20b`) use this one key |
| `GEMINI_API_KEY` | no | Without it the Gemini fallback stage is skipped |

Create it with `modal secret create huntloop-llm GROQ_API_KEY=... GEMINI_API_KEY=...`
or through Modal's dashboard. Nothing in this repo reads, writes, logs,
or needs the values.

**2. Deploy the Modal app.** `modal deploy
src/huntloop/modal_resume_functions.py` — the same command the existing
resume-processing functions already need, re-run after any change to
that file. It now also deploys `deep_dive_remote`.

**3. Set a dedicated Modal token on Render.** `MODAL_TOKEN_ID` and
`MODAL_TOKEN_SECRET` are declared in `render.yaml` with `sync: false`,
so Render prompts for them on the dashboard and no value is committed.
Create a **separate** token for the deployment (Modal → Settings → API
Tokens, or `modal token new`) rather than reusing a local development
one, so it can be revoked independently.

**4. Run the migration against Neon.** The feature needs the
`resume_deep_dives` table (revision `d5e7f9a1b3c6`). Run `alembic
upgrade head` against the Neon database yourself — nothing in this repo
migrates a live database automatically.

### Optional: a second Grafana datasource for Neon

The local observability stack provisions a PostgreSQL datasource (uid
`huntloop-postgres`, see
`observability/grafana/provisioning/datasources/datasource.yml`) that
backs the "HuntLoop Resume Deep-Dive" dashboard. It points at the
**local** database by default and is the only Postgres datasource that
exists.

Pointing Grafana at the live Neon demo database as well is **documented
here but deliberately not set up**, because doing it safely needs a
database role that this repo cannot create for you:

1. **In Neon, create a dedicated read-only role** — not the application
   role the API connects as. Something like:

   ```sql
   CREATE ROLE grafana_readonly LOGIN PASSWORD '<a-new-password>';
   GRANT CONNECT ON DATABASE <db> TO grafana_readonly;
   GRANT USAGE ON SCHEMA public TO grafana_readonly;
   GRANT SELECT ON ALL TABLES IN SCHEMA public TO grafana_readonly;
   ALTER DEFAULT PRIVILEGES IN SCHEMA public
     GRANT SELECT ON TABLES TO grafana_readonly;
   ```

   Every provisioned panel query is a plain `SELECT`, so `SELECT` is all
   this role ever needs. Granting it more would be strictly worse.

2. **Add a second datasource entry** alongside the existing one, with a
   different `uid` (e.g. `huntloop-postgres-neon`), its own
   `HUNTLOOP_NEON_PG_*` environment variables wired through the
   `grafana` service in `docker-compose.yml` the same way the local ones
   are, and `sslmode: require` (Neon requires TLS). Keep the committed
   file free of credentials — variables only, exactly as the local entry
   does it.

3. **Duplicate the dashboard, don't repoint it.** Change the copy's
   panel `datasource.uid` to the new one. Repointing the existing
   dashboard would silently turn a local-only view into one that reads
   live production data, which is precisely the mistake worth avoiding.

Until all of that is done deliberately, the Grafana stack cannot reach
Neon at all — which is the intended default.
