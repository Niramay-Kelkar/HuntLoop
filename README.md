# HuntLoop

HuntLoop is a multi-ATS job-board scraper and matching app. `main.py` is a
multi-platform orchestrator that scrapes public job-board APIs across seven
implemented ATS platforms (Greenhouse, Lever, Workday, SmartRecruiters,
Ashby, iCIMS, Gem — see `CLAUDE.md` for the full per-platform detail) for
however many companies have been onboarded (`companies.ats_platform`), and
writes normalized postings (company, source, locations, skills, department,
employment type) into a Postgres database via SQLAlchemy/Scrapy. On top of
that, a FastAPI backend (`src/huntloop/api/`) and a Next.js frontend
(`frontend/`) expose the scraped postings with embedding-based resume match
scoring, precomputed matched/missing skills, optional H-1B/DOL-sponsorship
data, an application tracker, and a BYOK resume-grounded chat/drafting
assistant. See "Project status" below for what's built vs. not, and
`CLAUDE.md` for the full architectural detail this file doesn't repeat.

## Prerequisites

- Python 3.13 (what this repo's virtualenv is built with — no stricter pin
  exists yet) **except on macOS Intel (x86_64) — see the callout below,
  `pip install -r requirements.txt` does not complete there on 3.13.**
- PostgreSQL (developed/tested against Postgres 18, running locally)
- A Postgres role with privileges on the target database (`CREATE`, `SELECT`,
  `INSERT`, `UPDATE`, `DELETE` on its tables)

> **macOS Intel (x86_64) + Python 3.13: `pip install -r requirements.txt`
> fails on `torch`.** This is a real, confirmed PyPI wheel gap, not a bug in
> this repo's pins: `torch` is a transitive dependency (via
> `sentence-transformers`, used for resume/job-match embeddings —
> `src/huntloop/embeddings.py`), and PyPI's last `macosx_x86_64` `torch`
> wheel release, `2.2.2`, only ships `cp312` and earlier builds — there is no
> `torch` wheel for `cp313` on this platform at all (confirmed directly via
> `pip download --platform macosx_10_9_x86_64 --python-version 313
> --only-binary=:all: torch`, which fails with `no versions`, vs. the same
> command with `--python-version 312`, which resolves `2.2.2` cleanly). No
> version range in `requirements.txt` can fix this — it's an availability
> gap, not a pin.
>
> **If you're on macOS Intel, build the virtualenv with Python 3.12
> instead** (`python3.12 -m venv .venv` in the Setup steps below) — that
> resolves `torch==2.2.2` and `pip install -r requirements.txt` completes
> end to end with exit code 0 (verified in a real clean venv). Apple
> Silicon (arm64), Linux, and Windows on Python 3.13 are unaffected (real
> `cp313` wheels exist for those platforms).
>
> **Installing successfully is not the same as it working, though — read
> this before assuming Python 3.12 "fixes" embeddings on macOS Intel.**
> Even after a clean 3.12 install, `import sentence_transformers` (and
> therefore `import torch`) still crashes at runtime on this platform: the
> only `torch` build macOS x86_64 gets (`2.2.2`) was compiled against
> NumPy's pre-2.0 C ABI, while `pip`'s resolver — with no way to know about
> that ABI constraint — pulls the latest `numpy`/`scipy`/`scikit-learn`
> (which require NumPy ≥ 2.0) to satisfy everyone else's floor. The result
> is a real crash (`NameError: name 'nn' is not defined` inside
> `transformers/integrations/accelerate.py`), confirmed directly against
> the installed packages, not a hypothetical. Downgrading `numpy` alone
> just moves the same conflict onto `scipy` instead — there is no single
> `numpy` version that satisfies both `torch==2.2.2` and the rest of this
> stack's current releases at once. **This repo does not attempt to
> reconcile that dependency graph for macOS x86_64**, deliberately: the
> app already treats `sentence-transformers`/`torch` as an optional,
> gracefully-degrading capability everywhere it's used
> (`huntloop.pipelines.JobDataPipeline._get_reference_embedding()` and
> `_classify_and_embed()` both catch this exact failure via a broad
> `except Exception` and leave `is_relevant`/`embedding` `NULL` for that
> run rather than crashing — confirmed directly: `_classify_and_embed()`
> returns `(None, None)` under this real broken-import condition), and the
> project's real fix for actually running embeddings is Docker, not a
> local pin (see `src/huntloop/embeddings.py`'s own module docstring and
> `CLAUDE.md`). So: **use Python 3.12 on macOS Intel only to unblock `pip
> install -r requirements.txt` itself** (scraping, the API, the frontend,
> and most tests all work locally without `torch` ever successfully
> importing) — for anything that actually needs to compute a real
> embedding (`scripts/backfill_embeddings.py`, the daily scrape's
> classification step, etc.), run it inside the project's `app` Docker
> image (Linux, real `cp313` torch wheels, no ABI conflict) exactly as
> `CLAUDE.md` already documents, on any Python version.

## Setup

```bash
git clone <repo-url>
cd HuntLoop

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt

cp .env.example .env
# edit .env and fill in your real DATABASE_URL

alembic upgrade head

python scripts/seed_sample_job_postings.py
```

`.env.example` documents the expected format:

```
DATABASE_URL=postgresql+psycopg2://<user>:<password>@localhost:5432/<dbname>
```

The app fails fast with a clear error if `DATABASE_URL` is missing — see
`huntloop/settings.py`.

`alembic upgrade head` creates/updates all tables (`companies`, `job_sources`,
`job_postings`, `job_locations`, `job_skills`, `job_metadata`,
`lca_disclosures`, `sponsor_name_overrides`, `resume_versions`,
`job_applications`) to match the current schema.

`python scripts/seed_sample_job_postings.py` loads the same small real
sample dataset (~194 real job postings across 28 companies spanning all 7
implemented ATS platforms — see `data/samples/README.md`) the Docker
path's `seed` service auto-runs, so a manual/non-Docker install ends up
with the same populated-on-first-run experience instead of an empty
dashboard — this step is genuinely optional (the API/frontend below work
fine against an empty database, they'll just show zero jobs/companies
until you run a real scrape), but skipping it means starting from empty.
It only needs `DATABASE_URL` in the environment (same as everything
else here) — no Docker-specific assumptions (network hostnames,
hardcoded connection details) anywhere in the script. It's also
idempotent and safe to re-run: it checks `SELECT COUNT(*) FROM
job_postings` first and does nothing (logs one line, exits 0) if that's
already non-zero, whether from a previous run of this script or your own
real scrape — see the script's own module docstring for the full
reasoning.

At this point you have a fully migrated, sample-populated local database.
To actually run the app against it:

- **API**: `PYTHONPATH=src uvicorn huntloop.api.main:app --reload` — see
  "API service" below for the full endpoint list and options.
- **Frontend**: `cd frontend && npm install && npm run dev` — see
  "Frontend" below; by default it talks to `http://localhost:8000`, which
  is where the command above serves the API.

Verify it end-to-end:

```bash
curl http://localhost:8000/dashboard/stats
# {"total_jobs":194,"total_companies":28,"applications_by_status":{...},"new_jobs_last_7_days":194}
curl http://localhost:8000/jobs?limit=3
# real job postings from the sample dataset

open http://localhost:3000/dashboard   # or /jobs — real data, not an empty state
```

## Running the scraper

From the repo root:

```bash
python main.py
```

`main.py` is a multi-ATS orchestrator: it queries `companies.ats_platform`
(populated by the onboarding/detection scripts under `scripts/` — see
`CLAUDE.md` for the full detail on `detect_and_store_ats.py` and the
per-platform discovery scripts), groups companies by platform, and runs the
matching spider (`GreenhouseScraper`, `LeverScraper`, `WorkdayScraper`,
`SmartRecruitersScraper`, `AshbyScraper`, `IcimsScraper`, `GemScraper`) once
per platform with that platform's full token list. A company with an
unrecognized/`unknown`/`NULL` platform is skipped with a clear log message,
not silently dropped. Every spider pipes each scraped posting through the
same source-agnostic `JobDataPipeline`, which upserts the company/source and
inserts the job posting (plus its locations/skills/department/employment
type) into Postgres.

Expect Scrapy's standard crawl log, plus pipeline log lines for each item,
e.g.:

```
2026-08-19 12:55:04,123 [WARNING] huntloop.pipelines: [PIPELINE TRIGGERED] Processing item: Chief of Staff
2026-08-19 12:55:04,150 [INFO] huntloop.pipelines: Skipping reposted job 7913718
```

(`Skipping reposted job` means that `gh_job_id` was already in the database —
expected on any run after the first. On a genuinely new posting you'll instead
see `Inserted job: <title> for <company>`.) The run ends with Scrapy's stats
dump (`item_scraped_count`, `finish_reason: finished`, etc.) and:

```
2026-08-19 12:54:46,001 [INFO] huntloop.pipelines: JobDataPipeline closed
2026-08-19 12:54:46,002 [INFO] scrapy.core.engine: Spider closed (finished)
```

## Logging

All logging (the scraper, the pipeline, and the one-off scripts in
`scripts/`) goes through a shared setup in `huntloop/logging_config.py`:
consistent format (timestamp, level, module name, message), written to both
the console and a rotating log file at `logs/huntloop.log` (gitignored —
it's a runtime artifact, not source; capped at 5MB per file, 3 backups
kept).

The log level defaults to `INFO`. Override it with the `LOG_LEVEL`
environment variable, e.g.:

```bash
LOG_LEVEL=WARNING python main.py
```

## Scheduled runs

The scraper orchestrator (`main.py`), the skills-matching backfill
(`scripts/backfill_skills_matching.py`), and a third stage, the
department-categorization backfill (`scripts/backfill_department_category.py`),
all run on the same daily schedule, so postings stay fresh and
newly-scraped (or still-backlogged) jobs get matched against the active
resume and categorized without any manual step. This is local-only
automation for a dev machine that's actually on/awake at the scheduled
time — it is not a substitute for real deployment scheduling (e.g. GitHub
Actions against a hosted Postgres), which is a separate, later step once
there's a publicly reachable database (see `huntloop-architecture-decisions.md`
for research on that, not yet acted on).

**On this project's own dev machine the actual trigger is `launchd`, not
`cron`** — `cron` doesn't run a missed job when the Mac is asleep, which
made a real overnight 3am `cron` schedule silently never fire; see
`CLAUDE.md`'s Scheduling entries for the full launchd migration and its
boot/login catch-up job. The `crontab` instructions below still work as a
generic, portable way to schedule the same wrapper script on any machine
(cron is what most non-macOS setups would actually use) — they're not
wrong, just not what this specific dev machine runs day to day.

`scripts/run_orchestrator_cron.sh` is the entrypoint the scheduler calls
(the name predates the later `cron`→`launchd` migration above; it wasn't
renamed). It's a thin wrapper, not a parallel code path: it `cd`s into the
repo (the scheduler's working directory isn't otherwise predictable), then
runs each stage with `.venv/bin/python` (stage 1 now runs via
`docker compose run` instead — see `CLAUDE.md`'s "the daily scraper itself
now runs via Docker" entry) — the same scripts, same `.env`/
`DATABASE_URL`/`GROQ_API_KEY` a manual run uses. Nothing about credentials
or config is duplicated for the scheduled path. All three stages always
run, regardless of whether an earlier one succeeded — a scraping hiccup
shouldn't stall skills-matching or categorization progress on the backlog,
and vice versa. The skills-matching stage works through whatever
job_postings rows still have `matched_skills IS NULL` (best resume-match
score first, see `CLAUDE.md`), respecting each provider's real daily
free-tier budget, and simply stops itself cleanly for the day once that
budget is exhausted — picking back up automatically on the next scheduled
run. That's expected steady-state behavior, not a failure.

**Enable it** (runs daily at 3:00 AM local time):

```bash
(crontab -l 2>/dev/null; echo "0 3 * * * $(pwd)/scripts/run_orchestrator_cron.sh") | crontab -
```

**Check it's installed:**

```bash
crontab -l
```

```
0 3 * * * /Users/niramaykelkar/Desktop/HuntLoop/scripts/run_orchestrator_cron.sh
```

**Disable it** (removes just this line, leaves any other crontab entries
alone):

```bash
crontab -l | grep -v 'run_orchestrator_cron.sh' | crontab -
```

**Where to check whether a scheduled run happened, and what it did:**

- `logs/cron.log` — append-only, wrapper-level marker log. One
  start/end pair per run, with a per-stage breakdown and the exit code
  of each, e.g.:
  ```
  === HuntLoop scheduled run started 2026-08-22 03:00:01 PDT ===
  --- stage 1/2: scraper orchestrator (main.py) ---
  --- stage 1/2 finished with exit code 0 ---
  --- stage 2/2: skills-matching backfill (scripts/backfill_skills_matching.py) ---
  --- stage 2/2 finished with exit code 0 ---
  === run finished with exit code 0 (scrape=0, skills=0) at 2026-08-22 03:04:12 PDT ===
  ```
  A non-zero overall exit code (or an `ERROR: .venv/bin/python not
  found` line, if the venv itself is missing) means at least one stage
  failed. Check this file first — it's the fastest way to answer "did
  last night's run happen, and did it succeed." Note that the skills-
  matching stage stopping early with a `Backfill stopped early (daily
  quota exhausted)` line is normal, not a failure — it still exits 0.
- `logs/huntloop.log` — the same rotating, shared-logging file every
  manual run also writes to (see "Logging" above; 5MB cap, 3 backups).
  Scheduled runs show up in it exactly like manual ones — same format,
  same per-company INFO/WARNING lines, same Scrapy stats dump at the end
  for stage 1, and the same per-job/per-batch INFO/WARNING lines for
  stage 2 (including which jobs got a skills match, which were rejected
  by the sanity filter, and why). This is where to look for *what*
  happened during a run, not just whether it succeeded.

The scheduled job assumes Postgres is already reachable at
`DATABASE_URL` when it fires (same requirement as a manual run — this
wrapper doesn't start or manage any database). On this repo's dev
machine that's a locally-installed Postgres 18 running as a system
service on port 5432, independent of the `docker-compose` `db` service
described below (which is a separate, smaller Postgres instance on port
5433 for the containerized workflow — see "Run with Docker"). If
`DATABASE_URL` points somewhere not currently running, the run fails
fast with a clear connection-error traceback in `logs/huntloop.log`,
same as it would for a manual run.

## Running tests

```bash
pytest
```

The suite has grown well past the original pipeline smoke tests (300+
tests as of this writing, across the pipeline, spiders, ATS
detection/onboarding, matching, the API, and more) — don't expect an
exact count to stay accurate here; run `pytest` for the real current
total. Illustrative early example, still representative of the format:

```
============================= test session starts ==============================
collected 2 items

tests/test_pipeline.py::test_process_item_inserts_job_posting PASSED     [ 50%]
tests/test_pipeline.py::test_duplicate_job_url_hits_integrity_error_handler PASSED [100%]

============================== 2 passed in 1.00s ===============================
```

Tests run against a throwaway Postgres schema created for the test session
(same server as `DATABASE_URL`, different namespace) — they never read or
write your real `job_postings` data. See `tests/conftest.py`.

## Run with Docker

Runs the full stack — Postgres 18, the scraper/pipeline (`app`), the API
(`api`), and the frontend — as containers via docker-compose. This is a
separate Postgres instance from any local one — it's exposed on host port
`5433` (not `5432`) so there's no ambiguity about which database gets
written to, and its data lives in a named volume (`pgdata`) that persists
across `docker-compose down`/`up`.

```bash
cp .env.example .env
# edit .env: fill in DATABASE_URL, POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB
# (the containerized app builds its own DATABASE_URL from the POSTGRES_* vars,
# pointed at the `db` service, so DATABASE_URL itself only matters if you also
# run things locally against localhost:5432)

docker compose up --build
```

That's it — **no separate manual `alembic upgrade head` step is needed
anymore.** A one-shot `seed` service (see `docker-compose.yml`) runs once,
automatically, before `app`/`api` are allowed to start: it applies every
pending migration, then seeds a small real sample dataset (~194 real job
postings across 28 companies spanning all 7 implemented ATS platforms —
see `data/samples/README.md` for exactly what's in it) into a fresh
database, so the first thing you see — the dashboard, the job list, the
API's own responses — is populated with real data, not empty. The sample
seed is gated on `job_postings` being genuinely empty: run
`docker compose up` again later, after you've onboarded your own companies
and run a real scrape, and the `seed` service's logs will show it
detecting your real data and skipping the sample seed entirely (`docker
compose logs seed`) — it never re-runs or overwrites anything once real
data exists, whether that's the sample itself or your own scrape.

`docker-compose down -v` tears everything down, including the `pgdata`
volume, for a clean slate (the next `docker compose up` re-seeds the
sample dataset from scratch, since the volume — and therefore
`job_postings` — is empty again).

**`docker compose up` (no extra flags, no `--profile`) brings up the
full stack** — `db`, `app`, `api`, and `frontend` together, with no
separate `npm run dev`/`uvicorn --reload` step required. See "API
service" and "Frontend" below for what each one is.

## Troubleshooting

### `docker compose up` fails with "Pool overlaps with other one on this address space"

```
Error response from daemon: invalid pool request: Pool overlaps with
other one on this address space
```

**Cause:** `docker-compose.yml` pins this project's Docker network to a
fixed subnet, `172.28.0.0/24` (needed so the optional Caddy reverse proxy
has a stable IP for the `api` service to trust — see the `networks`
block at the bottom of `docker-compose.yml` for the full reasoning).
`172.28.0.0/24` is a common private range other Docker Compose projects
also default to or hand-pick, so if any other Compose project already
running on this machine — or a second clone/checkout of this repo — has
a network on that same subnet, Docker refuses to create a second one
that overlaps it.

**Fix:** pick a different, unused subnet and set it via `.env` — no code
changes needed. First, check what's actually taken:

```bash
docker network ls
docker network inspect <network-name>   # look for its "Subnet" under IPAM.Config
```

Then in your `.env`, set both variables together (they must describe the
same network — `HUNTLOOP_CADDY_IP` has to be a `.x` address that falls
inside `HUNTLOOP_DOCKER_SUBNET`):

```bash
HUNTLOOP_DOCKER_SUBNET=172.31.0.0/24
HUNTLOOP_CADDY_IP=172.31.0.10
```

Then re-run `docker compose up`. If you're not using the `proxy` profile
(Caddy) at all, `HUNTLOOP_CADDY_IP` still needs to be set consistently
with `HUNTLOOP_DOCKER_SUBNET` — it's read either way, it just has no
effect unless the `caddy` container actually exists. Both variables are
documented with their defaults in `.env.example`.

## API service

A FastAPI backend, `src/huntloop/api/main.py` - a separate service from
the scraper/orchestrator (`app`), not merged into it. Its own `api`
service in `docker-compose.yml`, connected to the same bundled `db`
service `app` uses (not a separate/host Postgres) - self-contained for a
fresh `docker compose up`, and covered by the same `seed` service above,
so it never starts against an unmigrated or empty database. Point it at
your own real host Postgres instead by overriding `DATABASE_URL` in your
own `.env`, if you want that - no code change needed.

Endpoints (see `CLAUDE.md` for full per-endpoint detail — this list is
kept to the shape, not every query param/edge case):

- `GET /health` - basic liveness check.
- `GET /jobs` - paginated list of job postings, each with its calibrated
  composite match score (`match_score`, embedding similarity blended with
  skills-match ratio - see `CLAUDE.md`) against the active resume,
  precomputed `matched_skills`/`missing_skills`, sponsor/salary-estimate
  info, and application status (defaults to `not_applied` if no
  `job_applications` row exists yet). Query params: `company`
  (case-insensitive filter), `department`, `employment_type`, `location`
  (repeatable, OR'd), `min_score` (0-1, requires an active resume),
  `salary_min`/`salary_max`/`salary_unspecified`, `sort` (`score` or
  `-score`, default `-score` - best matches first), `limit`/`offset`.
- `GET /jobs/{id}` - single job, same fields plus the full
  `job_description` (rendered as sanitized HTML by the frontend).
- `GET /jobs/departments` / `GET /jobs/employment-types` / `GET /jobs/locations`
  - the real distinct filter values behind the `GET /jobs` params above
  (departments/locations are canonicalized, not raw free text).
- `PATCH /jobs/{id}/application` - upserts the application status for a
  job (`{"status": "applied", "notes": "..."}`) - one row per job, not a
  history; `applied_at` is set the first time status moves away from
  `not_applied` and never overwritten by later status changes.
- `GET /dashboard/stats` - total jobs/companies, applications by status,
  new-jobs-in-the-last-7-days.
- `GET /resumes` / `POST /resumes/upload` / `PATCH /resumes/{id}/activate`
  - resume version history, PDF upload (extract → embed → activate), and
  reactivating a prior version.
- `POST /jobs/{id}/draft-answer` - BYOK (bring-your-own-key) resume-grounded
  drafting of free-form application answers via a user-supplied Groq/Gemini
  API key (`{"prompt", "provider", "api_key"}`) - the key is never read
  from server-side env vars, never persisted, never logged. **See the
  security note in "Reverse proxy / HTTPS for self-hosting" below before
  exposing this on anything but localhost** - it handles a real
  third-party credential per request.

Run it via Docker:

```bash
docker-compose up --build -d api
curl http://localhost:8000/health        # {"status":"ok"}
curl http://localhost:8000/jobs?limit=5  # real job postings + match scores
open http://localhost:8000/docs          # interactive OpenAPI docs
```

Or locally, without Docker (same `.venv`/`.env` as everything else):

```bash
PYTHONPATH=src uvicorn huntloop.api.main:app --reload
```

**CORS**: the API allows `http://localhost:3000`/`http://127.0.0.1:3000`
by default (the Next.js dev server's own default origin) via
`CORSMiddleware` - override with the comma-separated
`CORS_ALLOWED_ORIGINS` env var if the frontend runs somewhere else.
Browser requests need this; server-to-server calls (curl, `httpx`, etc.)
are unaffected either way, since CORS is a browser-enforced restriction.

## Frontend

A Next.js (App Router) app in `frontend/` - TypeScript, Tailwind CSS,
TanStack Query for data fetching. No longer a skeleton - real pages exist
for a dashboard (`/dashboard`, the default landing route), a filterable/
sortable job list with cards/table views (`/jobs`), a job detail page
(matched/missing skill chips, sponsor/salary-estimate sidebar, sanitized-HTML
job description, and a floating chat assistant panel with a read-only Q&A
mode plus the BYOK drafting endpoint above), an application tracker
(`/applications`, kanban + list views), and resume version management
(`/resumes`, upload/activate). `frontend/src/types/api.ts` and
`frontend/src/lib/api.ts` mirror the backend's real Pydantic
schemas/endpoints (kept manually in sync - no shared codegen, a known gap).
See `CLAUDE.md`'s frontend bullet for the full page-by-page detail.

**Now containerized** (`frontend/Dockerfile`, a `frontend` service in
`docker-compose.yml`) - `docker compose up` alone runs a production-style
build (`next build && next start`) and serves it on host port `3000`,
so a stranger's first run needs no local Node/npm install at all:

```bash
docker compose up --build -d api frontend   # or just `docker compose up --build` for the full stack
open http://localhost:3000
```

`NEXT_PUBLIC_API_URL` is baked in at build time (`next build` inlines
`NEXT_PUBLIC_`-prefixed vars into the browser bundle - see
`src/lib/api.ts`), passed as a Docker build arg in `docker-compose.yml`
rather than a runtime `environment:` entry. It defaults to
`http://localhost:8000` there too, matching the `api` service's own
host-mapped port - the browser talks to that port directly regardless of
whether either service is containerized, since none of this runs
container-to-container.

This doesn't replace local development - `npm run dev`'s hot reload (via
Turbopack) is still meaningfully faster to iterate against than a Docker
image rebuild loop, and remains the right way to actively work on the
frontend:

```bash
cd frontend
npm install
npm run dev
open http://localhost:3000
```

By default it talks to `http://localhost:8000` - override with
`NEXT_PUBLIC_API_URL` in `frontend/.env.local` (see
`frontend/.env.local.example`) if the API is running somewhere else. The
API must already be running for either workflow above - via Docker (per
"Run with Docker"/"API service") or `uvicorn ... --reload`.

## Reverse proxy / HTTPS for self-hosting (optional, opt-in)

**Not needed for local/localhost use - everything above this section is
completely unaffected by it.** Plain `docker compose up` never starts
this; it exists only for anyone who wants to run this stack on a real
server, reachable at a real domain, over real HTTPS instead of plain
HTTP. (The stack has no TLS anywhere on its own - see
`src/huntloop/drafting.py`'s and `src/huntloop/api/routers/drafting.py`'s
module docstrings for why that specifically matters before exposing
`POST /jobs/{id}/draft-answer`, which handles a user-supplied third-party
API key, on any public/non-localhost deployment.)

This uses [Caddy](https://caddyserver.com/) as an optional reverse proxy
in front of the existing `api`/`frontend` services, behind a `proxy`
Compose profile (same pattern as the `observability` profile above -
nothing starts unless you opt in). Caddy was chosen over nginx+certbot
and Traefik for this project specifically: automatic HTTPS needs only a
domain name in a Caddyfile (no separate certbot/acme-companion container,
no per-service ACME labels), it's a single ~24MB container, and that
matches a solo self-hoster's scale better than Traefik's per-service
label config or nginx-proxy's two-container/shared-volume setup.

### What you provide

- **A real domain name** (or subdomain) you own.
- **A DNS A-record** for that domain pointing at your server's public IP
  address.
- **Ports 80 and 443 reachable** on that server - open in any firewall,
  and forwarded if the server is behind NAT. Port 80 is required even
  though everything ends up served over HTTPS - Let's Encrypt's HTTP-01
  domain-validation challenge and Caddy's own HTTP→HTTPS redirect both
  need it.

### What the repo provides

- The `caddy` service in `docker-compose.yml` (behind `profiles:
  ["proxy"]`) and `caddy/Caddyfile`.
- A `caddy_data` named volume for Caddy's certificates and ACME account
  key. **This must persist** - repeatedly losing it and re-issuing risks
  Let's Encrypt's real rate limits (5 failed validations per
  account/hostname/hour, 5 duplicate certificates for the same hostname
  set per week), so don't tear it down with `down -v` unless you
  genuinely want to throw the certificate away.
- `caddy/Caddyfile` itself reads `{$HUNTLOOP_DOMAIN}` from the
  environment rather than a hardcoded example domain - Caddy refuses to
  start with it unset, so there's no way to accidentally run this
  against a placeholder hostname.
- Path-based same-origin routing: Caddy serves the frontend at `/` and
  reverse-proxies `/api/*` (prefix stripped) to the `api` service, whose
  own routes are all rooted at `/` (`/health`, `/jobs`,
  `/dashboard/stats`, ...) with no `/api` prefix of their own. Serving
  both under one HTTPS origin means the frontend can call a plain
  relative `/api` path instead of needing to know its own public domain
  baked in at Docker build time (`NEXT_PUBLIC_API_URL` is otherwise a
  `next build`-time constant - see the Frontend section above).

### How to enable it

```bash
# in .env:
HUNTLOOP_DOMAIN=yourdomain.com
NEXT_PUBLIC_API_URL=/api    # so the frontend calls the proxied same-origin path

docker compose --profile proxy up --build -d
```

`NEXT_PUBLIC_API_URL=/api` only matters for this profile - it changes
what gets baked into the `frontend` image at build time, so it must be
set *before* `--build` runs, and only when you're actually fronting the
stack with Caddy. Leaving it unset (the plain no-proxy path covered
earlier in this README) keeps building the frontend against
`http://localhost:8000` exactly as before.

CORS (`CORS_ALLOWED_ORIGINS`, see "API service" above) does not need any
change for this setup - same-origin path-routing means the browser never
makes a cross-origin request to the API in the first place, so CORS is
moot for traffic that goes through Caddy. Direct calls to the `api`
service's own published port (still available, same as always) are
unaffected either way.

### None of this replaces the plain-HTTP setup

`docker compose up` (no `--profile proxy`) still behaves exactly as
documented above - `api`/`frontend` still publish their own ports
directly, with no Caddy involved and no HTTPS. This section only adds an
opt-in HTTPS front door for a real-domain deployment; it changes nothing
for local/dev use.

## Observability stack (optional, opt-in)

Prometheus + Pushgateway + Grafana, for local metrics on scraping
activity. `main.py`'s orchestrator (`huntloop.metrics`) pushes real
per-run metrics to the Pushgateway once, as a batch, at the end of each
run — jobs scraped/inserted/skipped-as-duplicate/errored (all labeled by
company and source) plus the run's wall-clock duration. A pre-provisioned
Grafana dashboard visualizes them. Metrics are observability, not a hard
dependency: if the Pushgateway is unreachable, `main.py` logs one warning
and the actual scrape/DB-insert run completes normally regardless.

These three services live behind the `observability` Compose profile, so
a plain `docker-compose up` (regular dev work) never starts them:

```bash
docker-compose --profile observability up -d prometheus pushgateway grafana
```

- **Prometheus** — http://localhost:9090. Config at
  `observability/prometheus/prometheus.yml`; scrapes the Pushgateway
  (`pushgateway:9091`) every 15s, not the app directly — `main.py` is a
  run-to-completion batch job, not a long-lived process Prometheus could
  poll on its own schedule. Check a scrape target's health at
  http://localhost:9090/api/v1/targets or Status → Targets in the UI.
- **Pushgateway** — http://localhost:9091. Where `main.py` pushes each
  run's metrics before exiting (`huntloop_jobs_scraped_total`,
  `huntloop_jobs_inserted_total`, `huntloop_jobs_skipped_duplicate_total`,
  `huntloop_scrape_errors_total`, `huntloop_run_duration_seconds`). Try it
  manually with any metric name:
  ```bash
  echo 'huntloop_smoke_test_value 42' | curl --data-binary @- \
    http://localhost:9091/metrics/job/smoke_test
  ```
  then, after Prometheus's next 15s scrape, query
  http://localhost:9090/graph?g0.expr=huntloop_smoke_test_value for `42`.
- **Grafana** — http://localhost:3001 (host port `3001`, not Grafana's
  own default `3000` - that port is now the Next.js frontend's, both in
  local dev and via the containerized `frontend` service above, so
  Grafana's host-side port mapping was moved to avoid the collision;
  `docker-compose.yml`'s `grafana` service still maps to container port
  `3000` internally, which is irrelevant from the host). Login
  `admin` / `admin` by default — override with `GRAFANA_ADMIN_PASSWORD`
  in `.env`, local dev
  only, not a real secret). Both its Prometheus datasource
  (`observability/grafana/provisioning/datasources/datasource.yml`) and
  the **"HuntLoop Scraping Activity" dashboard**
  (`observability/grafana/provisioning/dashboards/huntloop-scraping.json`,
  loaded via the file provider at
  `observability/grafana/provisioning/dashboards/dashboards.yml`) are
  pre-provisioned on container start — no manual "Add data source" or
  "Import dashboard" click-through needed on a fresh bring-up. The
  dashboard has 4 panels: jobs scraped over time (by company), jobs
  inserted vs. skipped-as-duplicate (by company), scrape error count, and
  run duration trend — all querying the metrics above directly, no new
  ones. Verify the datasource is connected via the API rather than just
  the UI:
  ```bash
  curl -u admin:admin http://localhost:3001/api/datasources
  # then, using the "uid" from that response:
  curl -u admin:admin http://localhost:3001/api/datasources/uid/<uid>/health
  ```

Tear down with `docker-compose --profile observability down` (add `-v` to
also drop the `prometheus_data`/`grafana_data` volumes). This doesn't
touch `db`/`app` or their `pgdata` volume — the two stacks are
independent.

## H-1B sponsorship data (optional)

**None of this is required to use HuntLoop.** Job scraping, matching, and
the rest of the app work fully with zero H-1B/LCA data loaded — sponsor
status just shows as "not checked" and salary estimates are simply absent
until you set this up. Nothing crashes or degrades in any other way
without it. Think of this section as an optional enhancement layer, not a
setup requirement.

With it, HuntLoop can additionally show whether a company has recent DOL
H-1B (LCA) sponsorship history and a rough salary estimate derived from
that company's own wage filings, by matching scraped companies against
real DOL LCA disclosure data in the `lca_disclosures` table
(`scripts/ingest_lca_disclosures.py` → `scripts/resolve_sponsor_matches.py`).

There is no automated fetching of this data anywhere in this repo, and
there won't be — DOL's disclosure files are downloaded manually, by
design, so nothing in this codebase ever crawls or scrapes a government
website. Two ways to get data in:

### Option A — real, current, full DOL data

1. Go to DOL's Foreign Labor Certification Data Center performance page:
   https://www.dol.gov/agencies/eta/foreign-labor/performance — the "LCA
   Disclosure Data" accordion section lists one Excel file per fiscal
   year/quarter (H-1B, H-1B1, and E-3 combined).
2. Download the most recent quarterly file. As of this writing the latest
   is FY2026 Q3, named `LCA_Disclosure_Data_FY2026_Q3.xlsx`, linked
   directly at `https://www.dol.gov/media/LCA_Disclosure_Data_FY2026_Q3.xlsx`
   (an `.xlsx` file, currently ~240 MB). Download whichever quarter(s) you
   want — more quarters means more historical sponsorship data, but even
   one quarter is enough to get sponsor matching working.
3. Place the downloaded file(s) in `data/raw/dol_lca/` (create the
   directory if it doesn't exist — it's gitignored, since these are large
   raw downloads, not something this repo ships). Keep the original
   filename exactly as downloaded (`LCA_Disclosure_Data_FY<YYYY>_Q<N>.xlsx`)
   — the ingestion script parses the fiscal year and quarter from the
   filename itself.
4. Run the ingestion script:
   ```bash
   python scripts/ingest_lca_disclosures.py
   ```
   This is idempotent (safe to re-run, e.g. after adding a new quarter's
   file) and only ingests `Certified`/`Certified - Withdrawn` (i.e.
   actually-approved) rows.
5. Link scraped companies to this data:
   ```bash
   python scripts/resolve_sponsor_matches.py
   ```
   Re-run this after ingesting more LCA data or scraping new companies.

### Option B — bundled sample dataset (quick demo, no download)

For a quick first look without downloading anything from DOL, load the
small real sample dataset committed at `data/samples/`:

```bash
python scripts/seed_sample_lca_disclosures.py
python scripts/resolve_sponsor_matches.py
```

This is a genuine subset of real DOL LCA disclosure data (not synthetic),
already ingested from DOL's own files — see `data/samples/README.md` for
exactly how it was built and what it contains (~7,200 rows, ~300 KB
compressed). It's meant to make a fresh install look populated and
demonstrate the sponsor-status/salary-estimate features quickly, not to
replace Option A's real/current/full dataset for actual job-hunting use.

## Adding a company to scrape

Not exposed via any UI or CLI flag — companies are onboarded by running one
of the discovery/detection scripts under `scripts/` (e.g.
`detect_and_store_ats.py`, `detect_ats_for_sponsors.py`,
`discover_and_store_ashby.py`, `discover_and_store_workday.py`,
`discover_and_store_icims.py`, `discover_and_store_smartrecruiters.py`,
`discover_and_store_gem.py`), which write a `companies` row with
`ats_platform`/`ats_token` (and, for Workday, `careers_url`) populated. Most
of these scripts gate auto-storing a match behind a confidence check and
hold ambiguous matches for human confirmation via a `--confirmations` file,
rather than guessing. See `CLAUDE.md` for the full per-platform detail on
each discovery mechanism and its accuracy/collision caveats. Once a company
has a row with a recognized `ats_platform`, `python main.py` picks it up on
its next run automatically — no separate step is needed.

## Project status

**Built:** a working multi-ATS scraper (Greenhouse, Lever, Workday,
SmartRecruiters, Ashby, iCIMS, Gem) feeding a Postgres pipeline
(companies/sources/postings/locations/skills/department/employment type);
embedding-based resume-to-job match scoring plus a Groq/Gemini-backed
matched/missing-skills engine; DOL H-1B/LCA sponsorship matching (optional,
see "H-1B sponsorship data" below); a FastAPI backend (`GET /health`,
`GET /jobs` with filtering/sorting/pagination, `GET /jobs/{id}`,
`GET /jobs/departments`, `GET /jobs/employment-types`, `GET /jobs/locations`,
`PATCH /jobs/{id}/application`, `GET /dashboard/stats`, `GET /resumes`,
`POST /resumes/upload`, `PATCH /resumes/{id}/activate`,
`POST /jobs/{id}/draft-answer`); a Next.js frontend covering a dashboard,
job list/detail, an application tracker, resume version management, and a
BYOK resume-grounded chat/drafting assistant on the job detail page; Alembic
migrations, a pytest suite (300+ tests), CI running migrations + both the
backend and frontend test suites against a real Postgres service on every
push/PR; Docker for the full stack (`db`/`app`/`api`/`frontend`, plus
opt-in `caddy`-fronted HTTPS and an opt-in Prometheus/Grafana observability
stack); and local (launchd-scheduled) daily scrape + skills-matching +
department-categorization automation. See `CLAUDE.md` for the complete,
much more detailed picture (this section is deliberately a summary, not a
substitute for it) and its "Current phase / what's next" section for what's
still explicitly deferred (broader test coverage of frontend presentation
components, an FK from `companies` to `lca_disclosures`, a hosted-DB/GitHub
Actions deployment, an automated `pip-audit`/`bandit` CI step, and more).
