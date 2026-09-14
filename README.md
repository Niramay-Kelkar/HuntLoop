# HuntLoop

HuntLoop is a job-posting scraper. Today, it scrapes a single company's public
Greenhouse job board API and writes normalized postings (with company, source,
locations, and skills) into a Postgres database via SQLAlchemy/Scrapy. The
broader roadmap is to aggregate postings across more sources and eventually add
sponsorship-aware matching — but that's future work, not something this
codebase does yet. What's below reflects only what's built.

## Prerequisites

- Python 3.13 (what this repo's virtualenv is built with — no stricter pin
  exists yet)
- PostgreSQL (developed/tested against Postgres 18, running locally)
- A Postgres role with privileges on the target database (`CREATE`, `SELECT`,
  `INSERT`, `UPDATE`, `DELETE` on its tables)

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
```

`.env.example` documents the expected format:

```
DATABASE_URL=postgresql+psycopg2://<user>:<password>@localhost:5432/<dbname>
```

The app fails fast with a clear error if `DATABASE_URL` is missing — see
`huntloop/settings.py`.

`alembic upgrade head` creates/updates all tables (`companies`, `job_sources`,
`job_postings`, `job_locations`, `job_skills`, `job_metadata`) to match the
current schema.

## Running the scraper

From the repo root:

```bash
python main.py
```

This runs the `GreenhouseScraper` spider against the Greenhouse Job Board API
for whatever companies are configured (see below), and pipes each scraped
posting through `JobDataPipeline`, which upserts the company/source and
inserts the job posting (plus its locations/skills) into Postgres.

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

The scraper orchestrator (`main.py`) and the skills-matching backfill
(`scripts/backfill_skills_matching.py`) both run on the same daily
schedule via `cron`, so postings stay fresh and newly-scraped (or
still-backlogged) jobs get matched against the active resume without any
manual step. This is local-only automation for a dev machine that's
actually on/awake at the scheduled time — it is not a substitute for
real deployment scheduling (e.g. GitHub Actions against a hosted
Postgres), which is a separate, later step once there's a publicly
reachable database.

`scripts/run_orchestrator_cron.sh` is the entrypoint cron calls. It's a
thin wrapper, not a parallel code path: it `cd`s into the repo (cron's
working directory isn't otherwise predictable), then runs each stage
with `.venv/bin/python` — the exact same scripts, same `.venv`, same
`.env`/`DATABASE_URL`/`GROQ_API_KEY` a manual run uses. Nothing about
credentials or config is duplicated for the scheduled path. Both stages
always run, regardless of whether the other succeeded — a scraping
hiccup shouldn't stall skills-matching progress on the backlog, and vice
versa. The skills-matching stage works through whatever job_postings
rows still have `matched_skills IS NULL` (oldest-scraped first),
respecting Groq's real 200,000-tokens-per-day free-tier budget, and
simply stops itself cleanly for the day once that budget is exhausted —
picking back up automatically on the next scheduled run. That's expected
steady-state behavior, not a failure.

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

Runs the scraper/pipeline and a Postgres 18 database as two containers via
docker-compose. This is a separate Postgres instance from any local one —
it's exposed on host port `5433` (not `5432`) so there's no ambiguity about
which database gets written to, and its data lives in a named volume
(`pgdata`) that persists across `docker-compose down`/`up`.

```bash
cp .env.example .env
# edit .env: fill in DATABASE_URL, POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB
# (the containerized app builds its own DATABASE_URL from the POSTGRES_* vars,
# pointed at the `db` service, so DATABASE_URL itself only matters if you also
# run things locally against localhost:5432)

docker-compose up --build -d db   # start Postgres and wait for it to be ready
docker-compose run --rm app alembic upgrade head   # first run only: create the schema

docker-compose up --build   # run the scraper (and keep db running)
```

`docker-compose down -v` tears everything down, including the `pgdata`
volume, for a clean slate.

**As of this entry, `docker compose up` (no extra flags, no `--profile`)
also brings up the `api` and `frontend` services** — the full stack
(backend + frontend), not just the scraper/db above, with no separate
`npm run dev`/`uvicorn --reload` step required. See "API service" and
"Frontend" below for what each one is; this section's `app`/`db`
instructions are unchanged by that — `app` is still a run-to-completion
batch job (the scraper), not a long-running service, so it still needs
`alembic upgrade head` run once against a fresh `pgdata` volume before it
can insert anything, same as before.

## API service

A FastAPI backend, `src/huntloop/api/main.py` - a separate service from
the scraper/orchestrator (`app`), not merged into it. Its own `api`
service in `docker-compose.yml`, connected directly to the real host
Postgres (via `host.docker.internal`, same pattern used for the
embeddings backfill) rather than the docker-compose `db` service - see
that file's comment on `api` for why.

Endpoints:

- `GET /health` - basic liveness check.
- `GET /jobs` - paginated list of job postings, each with its
  embedding-based match score against the active resume, precomputed
  `matched_skills`/`missing_skills`, and application status (defaults to
  `not_applied` if no `job_applications` row exists yet). Query params:
  `company` (case-insensitive filter), `min_score` (0-1, requires an
  active resume), `sort` (`score` or `-score`, default `-score` - best
  matches first), `limit`/`offset`.
- `GET /jobs/{id}` - single job, same fields plus the full
  `job_description`.
- `PATCH /jobs/{id}/application` - upserts the application status for a
  job (`{"status": "applied", "notes": "..."}`) - one row per job, not a
  history; `applied_at` is set the first time status moves away from
  `not_applied` and never overwritten by later status changes.

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
TanStack Query for data fetching. Skeleton stage: one page proving real
connectivity to the API (`GET /health`, `GET /jobs`), not a real
job-list UI yet. `frontend/src/types/api.ts` and `frontend/src/lib/api.ts`
mirror the backend's real Pydantic schemas/endpoints already, ready for
that UI; `frontend/src/components/` is an empty placeholder for it.

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
- **Grafana** — http://localhost:3000 (login `admin` / `admin` by
  default — override with `GRAFANA_ADMIN_PASSWORD` in `.env`, local dev
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
  curl -u admin:admin http://localhost:3000/api/datasources
  # then, using the "uid" from that response:
  curl -u admin:admin http://localhost:3000/api/datasources/uid/<uid>/health
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

Not yet configurable. Companies are a hardcoded list in
`src/huntloop/spiders/greenhouse_spider.py`:

```python
company_tokens = [
    'checkr'
]
```

`company_token` is the slug Greenhouse uses in a company's public job board
URL (e.g. `checkr` for `https://job-boards.greenhouse.io/checkr`). To scrape
more companies today, add their tokens to that list directly in the source —
there's no config file or CLI flag for this yet.

## Project status

**Built:**
- Scrapy spider pulling job postings from one Greenhouse-hosted company's
  public API
- SQLAlchemy models + Postgres pipeline (companies, sources, postings,
  locations, skills, raw metadata)
- Alembic migrations, env-based config, a pytest scaffold covering the
  pipeline/DB-insert path
- A dev-oriented Docker setup (app + Postgres via docker-compose)

**Not built yet:**
- Scraping more than one hardcoded company, or sources other than Greenhouse
- Any job matching or sponsorship-based filtering (the `h1b_sponsorship`
  column exists on `companies` but nothing populates or reads it yet)
- An API or any user-facing interface
- A containerized test runner/CI, or a production-hardened deploy image
