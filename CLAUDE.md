# CLAUDE.md

Context for Claude Code sessions working in this repo. Keep this short — it's a
reference for fast orientation, not documentation. Full setup/usage detail lives
in README.md.

## Project overview

HuntLoop, today, is a multi-ATS job-board scraper (Scrapy: Greenhouse + Lever
implemented) that pipes postings into a Postgres database via SQLAlchemy, with
an ATS-detection layer (`detect_ats()`) driving which spider runs for which
company. The full loop: `detect_ats()` identifies a company's ATS platform
from its careers URL → `scripts/detect_and_store_ats.py` stores that in
`companies.ats_platform`/`ats_token` → `main.py` (the entrypoint) queries
those rows, groups by platform, and runs `GreenhouseScraper`/`LeverScraper`
once each with the full token list for their platform → each spider
normalizes into `JobPostingItem` → the single, source-agnostic
`JobDataPipeline` upserts companies/sources/postings/locations/skills.
Platforms without an implemented spider (ashby, workday) or companies with no
detected platform are skipped with a clear log message, not silently dropped.

Broader vision (not yet built): aggregate job postings across many sources
beyond Greenhouse/Lever, and add sponsorship-aware matching so candidates can
filter for companies that actually sponsor visas (e.g. H1B). Ashby and
Workday spiders are deliberately not built yet — `detect_ats()` already
identifies companies on those platforms (see `companies.ats_platform`), but
nothing scrapes them; treat that as planned, not present.

## Tech stack and conventions

- Package root: `huntloop` lives under `src/` (`src/huntloop/...`). Anything
  importing it needs `src/` on `sys.path` (see `main.py`, `pytest.ini`).
- Config: `.env` (gitignored) + `python-dotenv`, loaded in `settings.py`. Never
  hardcode credentials — `.env.example` documents the required shape.
- Migrations: Alembic, config at repo root (`alembic.ini`, `alembic/`).
- Tests: pytest, config at repo root (`pytest.ini`), tests live in `tests/`.
- **FastAPI backend service, `src/huntloop/api/`, added 2026-08-22 — has
  real endpoints now: `GET /health`, `GET /jobs`, `GET /jobs/{id}`,
  `PATCH /jobs/{id}/application`, `GET /dashboard/stats`, `GET
  /resumes`, `POST /resumes/upload`, `PATCH /resumes/{id}/activate`.**
  Runs as its own `api` service in
  `docker-compose.yml` (own container, port 8000 — deliberately not
  merged into `app`, a separate concern). **`api`'s `DATABASE_URL`
  points at `host.docker.internal:5432` — the real local system
  Postgres — NOT the docker-compose `db` service; `api` has no
  `depends_on: db` at all, it never talks to that service.** `api` also
  has a `./data/resumes:/app/data/resumes` volume mount, added
  2026-08-23 for `POST /resumes/upload` — `data/resumes/` is gitignored
  *and* dockerignored (real personal data), so without this mount an
  upload would only exist in the container's own writable layer and be
  lost on recreation; this makes uploads land in the same real
  `data/resumes/` directory `scripts/ingest_resume.py` already used.
  Building `api` requires real disk headroom in Docker Desktop's build
  VM (torch's CUDA-dependency wheels alone are ~2GB downloaded) — hit a
  real `No space left on device` build failure from accumulated build
  cache/dangling images during that step; `docker builder prune -af` +
  `docker image prune -af` fixed it (see SESSIONS.md). Run
  locally via `PYTHONPATH=src uvicorn huntloop.api.main:app --reload`.
  `huntloop.api.routers.jobs` computes match score at query time via
  pgvector against the active resume (same pattern as every prior ad
  hoc score query) and reads precomputed `matched_skills`/
  `missing_skills` from `job_postings` (Step 5) — doesn't call Groq
  itself. `job_applications` (migration `7d31cf7fed9c`) is one row per
  job (upserted via PATCH, not a history table); a job with no row is
  `not_applied` by default, never backfilled with a dummy row.
  **`huntloop.db_models.Vector` also schema-qualifies its distance
  operators now (`OPERATOR(public.<=>)` etc.), not just its DDL** — a
  bare `<=>`/`<->`/`<#>`/`<+>` doesn't resolve under
  `tests/conftest.py`'s isolated schema even with both operands cast to
  `public.vector`, confirmed directly via `psql` before fixing (see
  SESSIONS.md) — this is required for any future code comparing
  `Vector` columns via SQLAlchemy, not optional. **A SQLAlchemy
  `Enum(SomePythonEnum)` column needs `values_callable=lambda cls:
  [e.value for e in cls]`** if the enum's DB labels are lowercase
  `.value`s (as `ApplicationStatus`'s are) — without it, SQLAlchemy maps
  the Python member *name* instead and every read raises `LookupError`
  (caught by actually hitting the endpoint, not by writing the
  migration). **`CORSMiddleware` is configured** (added 2026-08-22 once
  `frontend/` needed it — a browser's CORS preflight `OPTIONS` 405'd
  with none configured, invisible to every prior server-to-server check
  since CORS is browser-only enforcement) — allows
  `http://localhost:3000`/`127.0.0.1:3000` by default, override via
  `CORS_ALLOWED_ORIGINS`. Don't add auth/further endpoints unprompted —
  see SESSIONS.md for what's still explicitly deferred.
  **`GET /dashboard/stats` (`huntloop.api.routers.dashboard`, added
  2026-08-23, API only — no frontend page consumes it yet) returns
  `total_jobs`/`total_companies`/`applications_by_status`/
  `new_jobs_last_7_days` via a real `DashboardStats` Pydantic schema
  (`huntloop.api.schemas.dashboard`), not a raw dict.**
  `applications_by_status` reuses the exact same
  `COALESCE(job_applications.status, 'not_applied')` pattern
  `huntloop.api.routers.jobs` already uses for a single job's
  `application_status` — grouped from `job_postings` with an outer join
  to `job_applications`, not a bare `GROUP BY status` on
  `job_applications` alone, so a job with no application row still counts
  as `not_applied` and the five counts always sum to `total_jobs`.
  Verified end-to-end against the real running system: every field
  cross-checked exactly against raw `psql` queries (not the app's own
  code path) — see SESSIONS.md for the real numbers
  (`total_jobs=605`, `total_companies=9`, all 605 `not_applied`,
  `new_jobs_last_7_days=605` since every real `job_postings` row was
  scraped within the last 7 days as of this entry).
- **Next.js frontend, `frontend/` (App Router, TypeScript, Tailwind,
  TanStack Query), added 2026-08-22, real job-list UI added 2026-08-23,
  reskinned + extended with a job detail page and an applications tracker
  2026-08-23 (see below and SESSIONS.md).**
  `frontend/src/types/api.ts` hand-mirrors the backend's Pydantic
  schemas (no shared codegen — kept manually in sync, a known gap);
  `frontend/src/lib/api.ts` is a real fetch client (`getHealth`/
  `getJobs`/`getJob`/`updateApplicationStatus`/`getDashboardStats`),
  nothing mocked.
  **Routing, as of the 2026-08-23 dashboard step (see SESSIONS.md's
  "Real dashboard page" entry): `/` redirects to `/dashboard` (the
  mockup's home view); the job list itself lives at `/jobs`
  (`frontend/src/app/jobs/page.tsx`), not `/`.** The top nav
  (`frontend/src/components/NavBar.tsx`, a client component split out of
  `layout.tsx` for `usePathname()`-based active-tab highlighting) lists
  Dashboard/Jobs/Applications. Before this step, `layout.tsx`'s "Jobs"
  link and the job detail page's "← Back to jobs" link both pointed at
  `/jobs`, which didn't exist yet (the job list was mounted at `/`) —
  both silently 404'd; fixed as part of adding the dashboard, not a
  separate cleanup.
  **Deliberately NOT containerized** — `npm run dev`'s hot reload beats
  a Docker rebuild loop with no compensating benefit yet; revisit once
  there's an actual reason Docker helps (see SESSIONS.md). Run via `cd
  frontend && npm install && npm run dev`, needs the API already
  running (`NEXT_PUBLIC_API_URL`, defaults to `http://localhost:8000`).
  Note: this dev machine's `.venv` console-script shebangs went stale
  after the `JobSight`→`HuntLoop` rename — run the API via
  `PYTHONPATH=src .venv/bin/python -m uvicorn huntloop.api.main:app`,
  not the `uvicorn` script directly, until the venv is recreated.
  `ScoreIndicator`'s color gradient is calibrated to this app's real
  observed score range (green pinned at 0.6, not 1.0 — see SESSIONS.md's
  Step 3 histogram) — don't "fix" this back to a naive 0-1 scale, it
  would make nearly every real job render the same dull color.
  `JobSummary`/`JobDetail` also carry `locations: list[str]` (populated
  via the existing `JobPosting.locations` relationship, no migration
  needed).
- Entrypoint: `python main.py` runs the multi-ATS orchestrator end-to-end
  — queries `companies.ats_platform`, groups by platform, and runs
  `GreenhouseScraper`/`LeverScraper` once each with all tokens for that
  platform (see the architectural decisions below). `scrapy crawl` is not
  a supported invocation path — there is no `scrapy.cfg` at the repo
  root (deliberate, see `05b833c`'s commit message); always run spiders
  via `main.py` or programmatically (`process.crawl(SpiderClass, ...)`).
- Docker: `Dockerfile` + `docker-compose.yml` (app + postgres:18) for a
  dev-oriented containerized setup. CI (`.github/workflows/ci.yml`) runs
  migrations + pytest against a real Postgres service container on every
  push/PR to `master`. The app container runs as a dedicated non-root
  `huntloop` user (not root) — see the security audit entry in
  SESSIONS.md (2026-08-21). `.dockerignore` excludes `data/raw/` and
  `logs/` (mirroring `.gitignore`) so real LCA data and log output never
  get baked into an image layer.
- **`docker-compose.yml` has a Prometheus/Pushgateway/Grafana
  observability stack, added 2026-08-22 (see SESSIONS.md), gated behind
  `profiles: ["observability"]` so plain `docker-compose up` never starts
  it. This is now closed out — infra, real metrics, and a dashboard all
  exist.** Config lives under `observability/`:
  `observability/prometheus/prometheus.yml` (scrapes only the
  Pushgateway — `main.py` is a run-to-completion batch job, not something
  Prometheus could poll directly);
  `observability/grafana/provisioning/datasources/datasource.yml`
  (pre-provisions the Prometheus datasource with an explicit
  `uid: prometheus`, no manual UI setup); and
  `observability/grafana/provisioning/dashboards/` (a `dashboards.yml`
  file-provider config plus `huntloop-scraping.json`, the "HuntLoop
  Scraping Activity" dashboard — 4 panels: jobs scraped over time by
  company, jobs inserted vs. skipped-as-duplicate by company, scrape
  error count, run duration trend — all pre-provisioned, auto-loaded on
  container start, no manual import). Verified end-to-end against a real
  `python main.py` run: Grafana's own datasource proxy returned the same
  metric values as a direct Prometheus query, matching the run's actual
  DB row-count delta. Don't add more dashboards/panels unprompted unless
  there's a real new need.
- **`main.py`'s orchestrator pushes real per-run metrics to the
  Pushgateway, added 2026-08-22 (see SESSIONS.md).**
  `src/huntloop/metrics.py` holds a module-level `CollectorRegistry` (not
  `prometheus_client`'s global default) with `Counter`s
  `huntloop_jobs_scraped_total`/`huntloop_jobs_inserted_total`/
  `huntloop_jobs_skipped_duplicate_total`/`huntloop_scrape_errors_total`
  (all labeled `company`/`source`) and a `Gauge`
  `huntloop_run_duration_seconds`. `JobDataPipeline.process_item()`
  increments the per-item counters; `GreenhouseScraper`/`LeverScraper`'s
  `parse()` increment `scrape_errors_total` for malformed/non-JSON
  responses (errors that never reach the pipeline as an item).
  `run_multi_ats_scrape()` wraps the whole run in `try/finally` and the
  `finally` always sets `run_duration_seconds` and calls
  `push_run_metrics()` — one batched `push_to_gateway()` call at the end
  of the run, never per item. **`push_run_metrics()` never raises** — a
  Pushgateway-down failure logs one `WARNING`
  (`huntloop.metrics: Failed to push run metrics...`) and leaves the real
  scrape/DB-insert outcome untouched, same defensive principle as
  `detect_ats()`'s Playwright-failure handling. Verified end-to-end twice
  (see SESSIONS.md): a real run with the stack up, cross-checked
  metric-for-metric against DB row-count deltas; and a real run with the
  stack down, confirming the scrape/insert work completes normally and
  only a warning is logged, not a crash.
- One-off scripts live in `scripts/` (not part of the ongoing app pipeline
  or CI) — e.g. `scripts/ingest_lca_disclosures.py`, run manually. Uses
  `pandas`/`openpyxl` (in `requirements.txt`) to read DOL's `.xlsx`
  disclosure files from `data/raw/dol_lca/` (gitignored — see SESSIONS.md's
  Step 1 audit for how those files are obtained; they're downloaded
  manually, not fetched by any code in this repo).
- Logging: `src/huntloop/logging_config.py`'s `setup_logging()` is the
  single source of logging configuration (format, level, console + rotating
  `logs/huntloop.log` file handler) — every module gets its logger via
  plain `logging.getLogger(__name__)` and relies on this having already
  run. Level is controlled by the `LOG_LEVEL` env var (default `INFO`), not
  hardcoded per-module. `logs/` is gitignored (runtime artifact).
- **This dev machine has two distinct local Postgres instances — don't
  assume "the Postgres" means the Docker one.** `localhost:5432` is a
  system-installed PostgreSQL 18 (`/Library/PostgreSQL/18`, runs as a
  system service, not Homebrew's `postgresql@18` — that one fails to
  start here with "Address already in use" since the system one already
  holds the port) and is what `.env`'s `DATABASE_URL` points at and what
  manual `python main.py` runs actually use — confirmed 2026-08-22 (see
  SESSIONS.md) holding the real accumulated data (1,431,321
  `lca_disclosures` rows, 616+ `job_postings`, 10 `companies`).
  `localhost:5433` is `docker-compose`'s `db` service — a deliberately
  separate, smaller instance (see the Docker bullet above and README's
  "Run with Docker") that was holding only 115,695/45/1 rows respectively
  when checked. Don't conflate the two or assume either is stale/unused
  without checking row counts directly first.
- **Both Postgres instances now have pgvector 0.8.6 enabled — `docker-
  compose.yml`'s `db` (`pgvector/pgvector:pg18`, switched from
  `postgres:18`) and local system Postgres (5432, where the real data
  lives), as of 2026-08-22 (see SESSIONS.md for both entries). Still
  infra only — no vector columns, embeddings, or matching logic exist
  anywhere yet.** Enabled via Alembic migration `c2d25907fe8e`
  (`CREATE EXTENSION IF NOT EXISTS vector;`), confirmed applied on both
  (`alembic current` → `c2d25907fe8e (head)` on each) and verified via
  `\dx` (`vector 0.8.6`) plus a smoke-test `vector(3)` column/distance
  query on the Docker instance.
  **Local system Postgres needed pgvector compiled from source** against
  `/Library/PostgreSQL/18` (the EDB/PostgreSQL.org installer, not
  Homebrew) — three real obstacles hit and resolved along the way, not
  hypothetical: (1) Xcode Command Line Tools were registered
  (`xcode-select -p` returned a path) but not actually present — only
  caught by trying to compile something, not by `which cc`; fixed via
  `xcode-select --install`. (2) pgvector's default `-march=native` build
  flag isn't supported by Apple `clang` on a universal (`x86_64`+`arm64`)
  build, which is what this Postgres install's `pg_config` targets; fixed
  with `make PG_CONFIG=... OPTFLAGS=""`. (3) `CREATE EXTENSION vector`
  needs a real Postgres superuser (this pgvector version's `.control` has
  no `trusted = true`) — the app's `job_scraper` role correctly can't do
  it; the user ran it once as `postgres` (EDB's default superuser), after
  which `job_scraper`/Alembic can re-run the idempotent `CREATE EXTENSION
  IF NOT EXISTS` as a no-op indefinitely. Don't assume a future fresh
  Postgres install (a new dev machine, a rebuilt volume) has any of this
  done — re-check `\dx` and re-run this same sequence if not.
- **Scheduling is launchd, not cron, as of 2026-08-24 (see SESSIONS.md's
  "Migrate scraping schedule from cron to launchd" entry) — the original
  crontab entry (`0 3 * * *`) was confirmed to never fire reliably on
  this machine: `logs/cron.log` showed only 2 real runs ever, both at
  times that don't match 3am (10:39am and 9:45pm on 2026-08-22), both
  clearly manual/ad hoc, not cron-triggered. Root cause: cron does not
  run missed jobs when the Mac is asleep, and this laptop sleeps
  overnight with no wake schedule — 3am reliably never happened.**
  `~/Library/LaunchAgents/com.huntloop.scraper.plist` (a per-user
  LaunchAgent, gitignored-equivalent — it lives outside the repo, in
  home directory config, same as any other machine-local launchd job)
  runs the *same, unmodified* `scripts/run_orchestrator_cron.sh` wrapper
  (both its scraper + skills-matching stages — skills-matching wasn't
  touched in this step, it's being replaced by a continuous worker in a
  later step) via `StartCalendarInterval` (`Hour=3, Minute=0`), same
  time-of-day the crontab used. **The wrapper's own internals changed
  later (the scraper stage moved from `.venv` to Docker, 2026-08-24 —
  see the "daily scraper itself now runs via Docker" bullet further
  below) — the plist described here is still exactly what's installed,
  unchanged since this step.** **`man launchd.plist` confirms — not
  assumed — that unlike cron, launchd runs a missed
  `StartCalendarInterval` job the next time the machine wakes, coalescing
  multiple missed firings into one**, which is the actual reason this
  migration is expected to be reliable where cron wasn't; this still
  requires the machine to wake at some point in each 24h window (a Mac
  fully asleep for days would still not run it) — `pmset -g sched` shows
  this machine already has other apps' scheduled wake events registered,
  but no HuntLoop-specific `pmset` wake was configured in this step, since
  that requires `sudo` and is a separate, larger-blast-radius change
  (affects battery/other scheduled wakes) than what was asked; revisit
  only if launchd's wake-and-catch-up behavior alone proves insufficient
  in practice. The plist's `StandardOutPath`/`StandardErrorPath` both
  point at `logs/launchd.log` (new) — normally near-empty, since the
  wrapper script already redirects its own stdout/stderr internally into
  `logs/cron.log`; `logs/launchd.log` only catches failures *before* the
  wrapper's own redirection takes effect (exactly how a real failure was
  caught during setup — see below). `logs/cron.log`'s format and
  `logs/huntloop.log` are completely unchanged — only the trigger
  mechanism changed, not what runs or how it logs. **Verified end-to-end
  for real, not just "job loaded"**: `launchctl kickstart -p
  gui/<uid>/com.huntloop.scraper` force-fired the job immediately
  (evidence given `StartCalendarInterval` doesn't need to be awaited
  minute-by-minute to prove the job itself runs correctly under launchd —
  only the trigger differs from a real 3am firing, not the executed
  program/environment/logging); real `job_postings` row count went
  605→606 with `scraped_at` updated to the run's real timestamp, and
  stage 2 (skills-matching) processed real batches against the real Groq
  API, all visible in `logs/cron.log` with the exact same log format as
  every prior cron-triggered run. **Hit and fixed one real, non-obvious
  blocker along the way**: the first kickstart failed immediately with
  `Operation not permitted` (`shell-init: error retrieving current
  directory` / bash unable to even read the script) — macOS TCC privacy
  protection blocks processes spawned by launchd from accessing
  `~/Desktop` (and Documents/Downloads) by default, unlike an interactive
  Terminal session which already has that access; this repo happens to
  live under `~/Desktop/HuntLoop`. Fixed by granting Full Disk Access to
  `/bin/bash` (System Settings → Privacy & Security → Full Disk Access) —
  required on this machine specifically because the repo is under
  Desktop; not needed if a repo lived somewhere TCC doesn't gate. The old
  crontab entry (`crontab -l` had exactly this one line, nothing else) was
  removed via `crontab -r` only after the launchd version was confirmed
  working — both were never running simultaneously in production, only
  momentarily during this verification. This is still local-only
  automation; GitHub Actions scheduling against a hosted Postgres
  (Supabase/Neon) remains a deliberately separate, later deployment step —
  don't build it unprompted.
- **Resume ingestion exists (`resume_versions` table + `scripts/
  ingest_resume.py`), added 2026-08-22.** `data/resumes/` holds the
  actual PDF(s) and is gitignored + dockerignored (personal data, same
  reasoning as `data/raw/`) — never assume a PDF is present there; check
  before building anything that reads from it. Text extraction uses
  `pdfplumber` (not `pypdf`) for its layout-aware, `pdfminer.six`-based
  extraction — verified against a real resume: all sections extract in
  correct reading order with no jumbling, though bullet points come
  through as literal `(cid:127)` rather than `•` (a known pdfminer
  font-encoding limitation — now normalized away by
  `huntloop.text_cleaning.clean_text()` before embedding, see below).
  Each `scripts/ingest_resume.py` run inserts a new `resume_versions` row
  with an auto-incremented `version_number`, flips any previously-active
  row to `is_active=False` (never deletes it), and marks the new row
  active. **`extract_text()` now lives in `huntloop.resume_ingestion`
  (also home to `save_uploaded_pdf()`/`RESUMES_DIR`), added 2026-08-23 —
  `scripts/ingest_resume.py` imports it from there instead of defining
  its own copy, so it can't drift from the real API endpoint below.**
  **A real resume-management API now exists on top of this table
  (`huntloop.api.routers.resumes`, added 2026-08-23, see SESSIONS.md's
  "Resume management API endpoints" entry) — `GET /resumes` (version
  history with a short `text_preview`, not the full text), `POST
  /resumes/upload` (real PDF upload → extract → embed → insert active →
  deactivate the old active row), and `PATCH /resumes/{id}/activate`
  (reactivate an existing version, computing its embedding first if
  somehow missing; a no-op if it's already active).** Both
  activation-changing endpoints reset `matched_skills`/`missing_skills`
  to NULL on every `job_postings` row, so the existing daily cron
  (Step 5.5) naturally reprocesses everything against whichever resume
  is now active — **this reset must bind `sqlalchemy.null()`, not plain
  Python `None`, in the `update(JobPosting).values(...)` call.** Binding
  `None` on this `JSON` column stores the literal JSON scalar `null`,
  not a real SQL `NULL` (`matched_skills IS NULL` is `false`,
  `matched_skills::text` is `'null'`) — found live against real
  Postgres, not caught by the ORM-level `assert row.matched_skills is
  None` (which passes either way, since `json.loads('null') == None`
  too). That silently breaks `scripts/backfill_skills_matching.py`'s own
  `.filter(JobPosting.matched_skills.is_(None))` reprocessing query —
  those rows would never be picked up again. Don't revert this to plain
  `None`; `tests/test_api_resumes.py` asserts the same
  `.filter(...is_(None))` count directly, not just the ORM-level value,
  specifically to catch this class of bug again. **`GET /jobs`'s
  match-score query already resolves "the active resume" dynamically at
  query time** (`ResumeVersion.filter_by(is_active=True).first()`, fresh
  per request, no caching) — re-confirmed by reading that code again
  during this step and by a real test
  (`test_activating_a_different_resume_changes_live_match_scores`) that
  swaps the active version mid-test and asserts live scores change on
  the very next request. `huntloop.embeddings.embed_text()` is imported
  **lazily** inside these two endpoints (not at module level) since it
  needs torch, unavailable in this project's local dev venv (see
  below) — a module-level import would break importing the whole API
  locally, not just these two endpoints, since `huntloop.api.main`
  imports every router together. **The frontend now has a real
  `/resumes` page wired to this API, added 2026-08-24 (see SESSIONS.md's
  "Real resume management page" entry) — frontend-only, no backend
  changes.** `frontend/src/app/resumes/page.tsx`: a dropzone (click or
  drag-and-drop, client-side `.pdf`-only validation) driving
  `uploadResume()`, and a version-history list driving `activateResume()`
  per non-active row — both wired to `lib/api.ts`'s real
  `getResumes`/`uploadResume`/`activateResume`. `uploadResume()`
  deliberately bypasses the shared `apiFetch()` helper (which always
  sets `Content-Type: application/json`) since a `multipart/form-data`
  upload needs the browser to set its own boundary-bearing header. A
  successful upload or activation invalidates `["resumes"]`, `["jobs"]`,
  `["job"]`, and `["dashboard-stats"]` together — a resume swap changes
  every job's live score and resets its skills match, so all of those
  views need to stop showing stale data, not just the resumes list.
  `NavBar.tsx` gained a fourth tab, "Resume", alongside
  Dashboard/Jobs/Applications. The mockup's AI-resume-review column
  (missing keywords/phrasing suggestions/formatting notes) is
  deliberately **not** built — no backend for it exists yet; still a
  separate, not-yet-started phase.
- **Embedding-based match scoring exists, added 2026-08-22 (Step 3, see
  SESSIONS.md) — scoring mechanism only, no 70%-threshold wiring or
  LLM-suggestion logic yet (a Groq-based matched/missing skills-list
  does now exist as a separate piece — see the next bullet).**
  `huntloop.embeddings`
  wraps `sentence-transformers`' `all-MiniLM-L6-v2` (CPU-only, 384-dim -
  confirmed against the model's own published config, matches
  `EMBEDDING_DIM` in `db_models.py`); `huntloop.text_cleaning.clean_text()`
  strips HTML (job descriptions - checked real data: Greenhouse rows
  come back HTML-entity-escaped, Lever rows as raw HTML, one
  `html.unescape()` handles both) and `(cid:N)` pdfminer artifacts
  (resume text) before anything gets embedded. `resume_versions` and
  `job_postings` both have a nullable `embedding vector(384)` column
  (migration `08af7f0a020c`). **`huntloop.db_models.Vector` is a
  required subclass of `pgvector.sqlalchemy.Vector`, not just a style
  choice — it schema-qualifies DDL as `public.vector(n)`. Do not replace
  it with the bare `pgvector.sqlalchemy.Vector` or add another
  `vector`-typed column using anything else — see the conftest.py
  incident bullet above for exactly why.**
  `scripts/backfill_embeddings.py` embeds the active resume (recomputed
  every run) and backfills `job_postings` in batches of 100 (only
  `embedding IS NULL` rows, safe to interrupt/resume). **This project's
  local dev venv (macOS, Intel, Python 3.13) cannot run
  `sentence-transformers`/`torch` — confirmed by actually trying to
  install `torch` and finding no compatible wheel (PyPI's last
  macOS-x86_64 torch build, 2.2.2, tops out at Python 3.12).** Run
  `backfill_embeddings.py` inside the `app` Docker image instead (Linux,
  real `torch` wheels exist for cp313), pointed at the real local
  Postgres via `docker compose run --rm -e
  DATABASE_URL="...@host.docker.internal:5432/<db>" app python
  scripts/backfill_embeddings.py` — not the docker-compose `db` on 5433.
  Match scores are **computed at query time** via pgvector's `<=>`
  cosine-distance operator (`similarity = 1 - distance`), deliberately
  **not stored** — with one active resume and ~600 jobs a live query is
  trivial, and a stored score would need an invalidation mechanism (on
  every scrape/resume update) that doesn't exist; revisit only if live
  scoring ever becomes measurably slow. Verified end-to-end against the
  real resume + all 605 real job postings: healthy, non-degenerate score
  distribution (min 0.033, max 0.593, mean 0.372) and a by-eye-sane
  top/bottom-5 ranking (top 5 all Palantir "Software Engineer" roles;
  bottom 5 fraud-ops/creative/marketing roles) — see SESSIONS.md for the
  full numbers.
- **Matched/missing skills-list via Groq exists (`huntloop.skills_matching`),
  added 2026-08-22. Phase 3's matching engine (embeddings + scoring +
  skills matching) is complete and self-sustaining as of 2026-08-22 —
  not "fully backfilled" (529/605 job_postings rows are still NULL as of
  this entry and that number only shrinks gradually), but no manual
  step is needed for it to keep shrinking or to keep up with newly-
  scraped jobs. See SESSIONS.md for full detail on both entries below.**
  `GROQ_API_KEY` required in `.env`, fails fast at import time of this
  module only (not `settings.py`). **Model is `openai/gpt-oss-20b`, not
  a Llama variant** — checked live against `/v1/models` before picking
  anything; no Llama 3.x chat models are active on Groq's free tier.
  Re-check `/v1/models` before assuming any model name still exists —
  the free-tier lineup already changed once during this project.
  `job_postings.matched_skills`/`missing_skills` (JSON, nullable,
  migration `0fdafe5d162e`) are precomputed and stored, not recomputed
  live. `match_skills()` (single job/call, Step 4) is untouched;
  `match_skills_batch()` (resume once + 5 job descriptions per call) is
  the real production path — batching is genuinely ~2.3x more
  token-efficient with no quality cost, measured before adopting it.
  **Three separate real Groq limits exist, all found only by actually
  running things at increasing scale, not by reading docs — don't
  assume any is the only one:** (1) 8000 TPM rolling per-minute cap;
  (2) an ~8000-token **hard cap on a single request**, independent of
  the rolling window (why batches are capped at size 5, not just
  token-estimated); (3) a **200,000 tokens-per-day (TPD) cap** —
  `match_skills_batch()` raises `DailyQuotaExhausted` specifically for
  this (every other failure still returns `None`), and
  `backfill_skills_matching.py` stops the whole run cleanly on that
  signal (confirmed working live: a real cron trigger hit a genuine TPD
  429 and stopped itself after processing 24 jobs, exit code 0 — not a
  crash, not a hang). **Skills-matching is now a stage of the daily
  orchestrator (`scripts/run_orchestrator_cron.sh`, same schedule as
  Step 5/7 — no separate schedule; that schedule is now launchd-driven,
  not cron-driven, see the Scheduling bullet above)** — stage 1 is the
  existing scraper (`main.py`), stage 2 processes `matched_skills IS
  NULL` rows oldest-`scraped_at`-first under the real daily budget, then
  stops itself when exhausted; both stages always run regardless of the
  other's outcome. This is the only mechanism now — it both works down
  the backlog over time and keeps every future day's newly-scraped
  postings matched, with nothing to manually re-trigger ever again.
  **Sanity filter**: `MAX_PLAUSIBLE_MATCHED_SKILLS = 20` in
  `huntloop.skills_matching`, enforced inside `match_skills_batch()` —
  rejects (logs + leaves NULL for reprocessing) any job whose
  `matched_skills` exceeds 20 items, since a real failure mode surfaced
  where the model dumps the entire resume's skills section verbatim
  instead of genuinely matching (seen at 26, 33, 33, and 53 items across
  4 real occurrences so far, most recently caught live in production,
  not just in the original discovery). Threshold chosen from real data —
  every genuine result observed has been 0-10 items; 20 sits in the
  middle of a clean, wide gap between that and the lowest known anomaly
  (26) — validated against all 76 real stored results with zero false
  positives before being trusted. Don't loosen this threshold without
  re-checking the real data distribution first, and don't assume
  `matched_skills` values that predate 2026-08-22 in the DB are
  trustworthy without checking their length against it.
  **A local-Ollama replacement was evaluated 2026-08-29 and rejected
  (NO-GO) — see SESSIONS.md.** `huntloop.skills_matching` stays on Groq.
  `src/huntloop/skills_matching_ollama.py` is a complete, contract-
  identical Ollama-backend port kept **intentionally unwired** (nothing
  imports it) so the experiment is reproducible on better hardware;
  `scripts/validate_ollama_skills_match.py` is its validation harness
  (9 Step 4/5 sample jobs + the Duolingo soft-match / Palantir
  Deployment-Strategist known cases; writes gitignored
  `scratch_ollama_validation.json`). On this dev machine (Intel
  i5-8257U, 2 cores, 8 GB RAM, no GPU) every RAM-viable model
  (`qwen2.5:3b-instruct`, `llama3.2:3b`) failed quality — full-resume
  dumps, matched/missing inversion, prompt-echo, repetition spirals,
  and did NOT reproduce the soft-match nuance — and `qwen2.5:7b-instruct`
  timed out at 900s/job with <1 GB RAM free. Don't re-attempt an Ollama
  cutover here without new hardware (Apple Silicon ≥16 GB or a GPU box);
  the recommended path is staying on Groq and living with its 200K-TPD
  cap (the daily incremental volume, ~1–2 relevant jobs/day, fits one
  day's budget easily; the full backfill is a one-time multi-day cost
  the existing self-pacing script already handles).
  **Google Gemini's free tier is the WIRED fallback provider behind Groq
  as of 2026-08-29 (Step K) — see SESSIONS.md + full writeup in
  `huntloop-architecture-decisions.md`.** Why it became load-bearing:
  the ATS expansion (9→380 companies) pushed daily relevant-postings
  volume to ~120/day while Groq's real free-tier throughput at the wider
  set's ~9.5k-char JDs is only **~58 jobs/day** (long JDs collapse Groq's
  batch to ~1.7); Gemini's ~2,200/day (500 RPD × real batch 4.4) covers
  the gap. **Real current Gemini free-tier limits (from AI Studio —
  Google removed the static per-model table from the docs on 2026-08-18,
  limits are per-account now): 2.5-gen models cut to 20 RPD (was 1,000),
  `gemini-2.5-flash-lite` 404s; the only viable model is
  `gemini-3.5-flash-lite` at 15 RPM / 250K TPM / 500 RPD.** Quality
  (11-job side-by-side): equivalent to Groq on clear-cut jobs, better on
  2 SWE roles Groq whiffed, but more conservative on the Duolingo
  soft-match — hence Groq stays *primary*, Gemini is fallback-only.
  **Wiring:**
  - `huntloop.skills_matching_errors` — shared `DailyQuotaExhausted` /
    `ProviderResponseInvalid` (both backends raise the same classes).
  - `huntloop.skills_matching_router` — `match_skills_batch(resume, jds,
    state)` dispatching Groq→Gemini. `SKILLS_MATCHING_PROVIDERS` env
    (default `groq,gemini`; `groq` alone = pre-routing behaviour, no
    GEMINI_API_KEY needed). Two failover triggers: `DailyQuotaExhausted`
    marks a provider spent for the whole run; `ProviderResponseInvalid`
    (Groq `json_validate_failed` 400, ~18% of calls) fails over **just
    that batch**, provider stays primary. Run stops only on
    `AllProvidersExhausted`.
  - Per-provider batch sizing + pacing: each backend owns `MAX_BATCH_SIZE`
    / `MAX_BATCH_ESTIMATED_TOKENS` / `TARGET_TPM` / `MAX_RPM` (Groq
    5/7000/6000/30; Gemini 5/16000/200000/14 — 16000 computed from 250K
    TPM ÷ 15 RPM; `MAX_RPM=14` because Gemini's small batches otherwise
    run at ~30 req/min over its real 15 cap). `TokenPacer` enforces both
    a tokens/min and a requests/min bound; `backfill_skills_matching.py`
    chunks incrementally, re-reading `router.batch_limits(state)` (a
    4-tuple) each batch so the caps flip when Groq exhausts mid-run.
  - `backfill_skills_matching.py` query now filters `is_relevant IS TRUE`
    (29,921 → 12,972 NULL rows; 57% were irrelevant) and logs the
    relevant-backlog count each run as a capacity leading indicator.
  Verified with a real `--limit 250` run: 242 stored / 8 NULL, split
  groq 11 / gemini 231 (Groq's TPD was pre-spent → `DailyQuotaExhausted`
  fired after 11; 19 `json_validate_failed` per-batch failovers). Full
  12,972 backlog clears in ~5.6 days at steady state.
  `scripts/validate_gemini_skills_match.py` (side-by-side harness, writes
  gitignored `scratch_gemini_validation.json`) is unchanged.
  `GEMINI_API_KEY` is in `.env`.
- **A hybrid keyword + embedding-similarity relevance pre-filter exists,
  added 2026-08-24 (see SESSIONS.md) — `job_postings.is_relevant`
  (nullable `Boolean`, migration `0900f3514ad2`), meant to flag whether
  a posting is a software-engineering/technical role at all before
  resume-matching or skills-analysis spend effort on it. Flags only —
  never deletes/filters rows out of `job_postings`.** Logic lives in
  `huntloop.relevance_filter`: `INCLUDE_KEYWORDS`/`EXCLUDE_KEYWORDS`
  matched against `job_title` (built by reading all 425 real distinct
  titles in the live dataset, not a generic guess — e.g. bare `"analyst"`
  and `"technical"` were deliberately left out after the real data showed
  them mostly attached to non-technical titles here, and `"finance"`/
  `"financial"` were tried as excludes and dropped after they'd have
  wrongly overridden the one real technical title containing "Finance",
  `Data Scientist, Finance`, to irrelevant), combined with cosine
  similarity between each job's `title+description` embedding
  (`huntloop.embeddings.embed_texts()`, same all-MiniLM-L6-v2 model as
  resume/job-match scoring) and one fixed `REFERENCE_TEXT` describing the
  *category* of technical work — deliberately not a resume, since this
  filter must behave the same regardless of which resume is active.
  **`EMBEDDING_SIMILARITY_THRESHOLD = 0.29`, chosen from real measured
  similarities** (`scripts/calibrate_relevance_threshold.py`, run once
  against the exact real known-relevant/known-irrelevant rows named in
  this task) — known-irrelevant real titles (Wealthfront Fraud
  Operations Specialist, Checkr Chief of Staff, Duolingo Creative
  Director) topped out at 0.2547; known-relevant real titles (3 real
  Palantir Software Engineer variants, Palantir Platform Engineer,
  Duolingo Senior Data Science Manager) started at 0.3240 — a clean,
  non-overlapping gap, 0.29 sitting at its midpoint. Combination:
  `is_relevant = (keyword_include OR embedding_similarity >= 0.29) AND
  NOT keyword_exclude` — keyword-exclude is an unconditional override
  (a title explicitly naming a non-technical function like legal/sales/
  marketing/tax/recruiting is a higher-confidence signal than a generic
  word like "engineer" appearing elsewhere in the same title, e.g.
  "Embedded Legal Engineer", "Marketing Engineer"), while include and
  embedding-similarity are OR'd since keywords alone would miss
  obliquely-worded technical titles and embedding similarity alone would
  need an unnecessarily conservative threshold on its own. **Deliberately
  does NOT reuse the existing `job_postings.embedding` column** — that
  one is computed from `job_description` alone for resume-match scoring
  (a separate, already-documented purpose); this filter computes its own
  `title+description` embedding on the fly per job during the backfill
  run instead of storing one, to avoid any risk of silently changing
  resume-match semantics. `scripts/backfill_relevance.py` (same
  batch-of-100/`IS NULL`/interrupt-safe pattern as `scripts/
  backfill_embeddings.py`) classified all 606 real rows in one pass (368
  relevant, 238 not, 0 left NULL) — verified against every known example
  named in the task via direct `psql` query, all correct. Two accepted
  MVP-level imprecisions found during spot-checking and left as-is
  (embedding-only false positives, no keyword involved): `"GRC Program
  Manager"` and `"Product Designer"` — not blockers, worth revisiting
  only if it turns out to matter in practice.
  **Wired into the live insert path as of 2026-08-24 (see SESSIONS.md's
  "Wire the relevance pre-filter into the live scrape pipeline" entry) —
  `JobDataPipeline.process_item()` (`src/huntloop/pipelines.py`) now
  calls `classify_relevance()` for every newly-inserted row, so
  `is_relevant` is populated at insert time, not left NULL for a later
  manual backfill.** `REFERENCE_TEXT`/`EMBEDDING_SIMILARITY_THRESHOLD`/
  `classify_relevance` were reused completely unchanged — nothing
  re-derived or re-tuned in this step. The reference-text embedding is
  cached once per `JobDataPipeline` instance (`self._reference_embedding`)
  — computed at most twice per full `main.py` run (one pipeline instance
  per spider/platform, confirmed via `main.py`'s `process.crawl()` call
  pattern), never once per row. `huntloop.relevance_filter.
  cosine_similarity()` is a small extracted helper (pure vector math,
  not tuned logic) now shared by the pipeline and both relevance
  scripts, replacing three separate copies.
  **A real, load-bearing environment constraint governs this wiring, and
  was resolved via an explicit user decision, not a silent choice**: the
  embedding half needs torch, but the actual daily-scheduled scraper
  (the `launchd` job from the prior step) runs `main.py` via this
  machine's local `.venv`, which — as established repeatedly elsewhere
  in this doc — cannot run torch at all here. `JobDataPipeline` therefore
  **degrades gracefully**: if `sentence-transformers`/torch isn't
  importable, it logs one warning per spider run (not per row) and
  leaves `is_relevant` NULL for that run's inserts, rather than crashing
  the scrape; the same applies to an isolated per-row embedding failure
  (any other exception), which leaves just that row's `is_relevant`
  NULL rather than rolling back its whole insert.
  **The "real scheduled scrapes leave is_relevant NULL" gap noted when
  this was first wired in is now closed, as of 2026-08-24 (see the next
  bullet and SESSIONS.md) — the daily scraper itself now runs via
  Docker, so this graceful-degradation path is no longer expected to
  ever trigger for the daily scrape specifically.** It's left in place
  (not removed) as a genuine safety net — a real per-row embedding
  failure, a future environment without Docker, etc. Verified end-to-end
  for real via `docker compose run --rm app python main.py` against the
  real local Postgres before the Docker migration: 5 genuinely new rows
  inserted, all 5 had `is_relevant` populated immediately (confirmed via
  direct `psql` query); re-running `scripts/backfill_relevance.py`
  immediately after found **0 rows left to classify** — the insert-time
  path and the batch-backfill path fully agree. `pytest`: 72/72 passing,
  including one new assertion (`test_process_item_inserts_job_posting`)
  directly exercising the graceful-degradation path (this test env also
  has no torch). Don't touch skills-matching/Groq/queue work when
  extending this — that's a deliberately separate, later step.
- **The daily scraper itself now runs via Docker, not the local `.venv`,
  as of 2026-08-24 (see SESSIONS.md's "Move the daily scrape to run via
  Docker" entry) — closing the gap noted in the bullet above.**
  `scripts/run_orchestrator_cron.sh`'s stage 1 (the scraper) now runs
  `docker compose run --rm --build -e DATABASE_URL=... app python
  main.py` instead of `.venv/bin/python main.py` — the same `app` image
  every other embedding-dependent script in this project already needs.
  Stage 2 (skills-matching) is untouched, still `.venv/bin/python
  scripts/backfill_skills_matching.py` — it only talks to Groq over
  HTTP, no torch dependency, no reason to move it. The launchd plist
  itself (`~/Library/LaunchAgents/com.huntloop.scraper.plist`) is
  unchanged — it already only invoked the wrapper script, so all the
  real changes live in the wrapper.
  **Two real environment gaps were found and fixed, not assumed away:**
  (1) launchd's job environment does not inherit an interactive shell's
  `PATH` — confirmed directly via `launchctl print`, which showed this
  job's own default `PATH` as just `/usr/bin:/bin:/usr/sbin:/sbin`,
  missing `/usr/local/bin` where Docker Desktop's `docker` CLI is
  symlinked on this machine; without a fix, `docker` would never be
  found at all. Fixed by having the wrapper script `export
  PATH="/usr/local/bin:$PATH"` itself, rather than relying on the
  plist. (2) `DATABASE_URL` is overridden per-invocation (`sed
  's/localhost/host.docker.internal/'` against the real value in
  `.env`) since `docker-compose.yml`'s own default for `app` points at
  its own small `db` service, not the real local system Postgres where
  the actual scraped data lives (see the two-Postgres-instances note
  below) — same substitution pattern already used for every prior
  manual `docker compose run` against this Postgres.
  `--build` is passed on every scheduled run so a stale image can never
  silently run in production — this is now the ongoing daily path, not
  a one-off manual invocation, so staleness would otherwise be
  invisible.
  **A separate, real logging regression was found and fixed while
  verifying this**: `docker-compose.yml`'s `app` service had no volume
  mount for `logs/`, so stage 1's rotating `logs/huntloop.log` file
  handler was writing only inside the container's own ephemeral
  filesystem — discarded on `--rm`, with only the console-handler
  output that happened to flow through to `logs/cron.log` surviving.
  Fixed the same way `api`'s `./data/resumes` mount already solves the
  identical class of problem: `app` now has a `./logs:/app/logs`
  volume mount too. A real host/container UID mismatch (host `niramaykelkar`
  vs. the container's `huntloop` user, uid 999) was checked directly,
  not assumed safe — confirmed by an actual write-then-read-from-host
  test that Docker Desktop's macOS file-sharing layer doesn't enforce
  strict POSIX ownership on bind mounts here, so this isn't a problem
  on this machine; worth re-checking if this project ever runs on
  Linux, where bind-mount permissions are enforced strictly.
  **Docker-down behavior, researched and reported per this task's
  explicit ask, not silently fixed**: simulated an unreachable Docker
  daemon (bad `DOCKER_HOST`, since deliberately quitting the user's real
  Docker Desktop mid-session felt too disruptive) — `docker compose run`
  fails immediately with a clear, specific error to stderr ("failed to
  connect to the docker API ... check if the path is correct and if the
  daemon is running") and exit code 1, not a silent failure or hang.
  Since the wrapper redirects all stage output into `logs/cron.log`,
  this is fully visible after the fact, and the wrapper's existing
  exit-code bookkeeping correctly reflects the failure.
  **The "Docker Desktop doesn't start at login" gap flagged here is now
  closed, 2026-08-24 (see SESSIONS.md's "Enable Docker Desktop
  autostart-at-login" entry) — Docker Desktop's "Start Docker Desktop
  when you sign in" setting is now enabled on this machine.** Verified
  via the real, live-authoritative config file
  (`~/Library/Group Containers/group.com.docker/settings-store.json`'s
  `AutoStart` key — the neighboring `settings.json` is stale/vestigial
  on this install, its mtime frozen since 2022, not what this Docker
  Desktop version actually reads) read `true` immediately after the
  change and **stayed `true` after a real `docker desktop restart`**
  (confirmed via a genuine PID change on the Docker Desktop GUI process,
  not just re-reading the file) — real evidence the app's own startup
  logic preserves this preference, not just that a checkbox looked
  checked. Also checked the macOS-level login-item registration directly
  via `sfltool dumpbtm` (Apple's Background Task Management inspector,
  not the legacy AppleScript login-items list, which doesn't reflect
  this kind of registration at all) — found the `DockerHelper` login
  item already `enabled` at the OS level both before and after, revealing
  the real two-layer mechanism: `DockerHelper` is a small, always-
  registered login launcher that itself checks Docker Desktop's
  `AutoStart` preference at each login to decide whether to actually
  launch the app. **A genuine full OS reboot was not performed** (would
  have killed the working session; felt disproportionate given two
  independent strong pieces of evidence already in hand) — the true
  first-real-reboot confirmation is still open, noted explicitly as
  such rather than assumed. **This is a local-machine-only fix,
  deliberately without any accompanying local monitoring/retry
  infrastructure, and will be entirely superseded if/when the scraper
  moves to a cloud deployment** (a managed container scheduler, or a
  Linux host running Docker Engine under `systemd` instead of Docker
  Desktop) — don't build on top of this Docker-Desktop-specific
  mechanism (`settings-store.json`, `DockerHelper`, `sfltool`) as if
  it were permanent infrastructure.
  **Verified end-to-end for real**: `launchctl kickstart -p
  gui/<uid>/com.huntloop.scraper` (same method as the original launchd
  migration) fired a real run that built/ran the `app` image, inserted 4
  genuinely new `job_postings` rows, all with `is_relevant` populated
  immediately (confirmed via direct `psql` query, 0 NULLs before and
  after), and finished stage 1 with a real exit code 0 — visible in
  `logs/cron.log` under the updated `"stage 1/2: scraper orchestrator
  (main.py, via docker compose run)"` label, including the real Docker
  build output. Re-ran `scripts/backfill_relevance.py` immediately after
  and got **0 rows left to classify** again. `pytest`: 72/72 passing,
  unchanged (this step touched no application code, only the wrapper
  script and `docker-compose.yml`).

## Key architectural decisions (already made — don't re-litigate)

- **`job_url` is the canonical unique key** on `job_postings`, not `gh_job_id`.
  `gh_job_id` isn't reliably unique and won't generalize to non-Greenhouse
  sources later.
- **`Company.h1b_sponsorship` is intentionally kept** even though it's a bare
  boolean today. It will likely be superseded by real sponsorship data —
  `LcaDisclosure` (`lca_disclosures` table) now holds raw DOL LCA disclosure
  records with `employer_name_normalized` populated for every row (see
  `src/huntloop/matching/normalize.py`), `find_matching_employers()`
  (`src/huntloop/matching/fuzzy_match.py`) ranks candidate
  `employer_name_normalized` values against a raw company name, and
  `get_sponsorship_summary()` (`src/huntloop/matching/sponsorship.py`)
  applies that to a real `Company` row to answer "does this company
  sponsor, and how much." Don't "fix" `h1b_sponsorship` in the meantime by
  removing or redesigning it unprompted.
- **There is deliberately no FK from `companies` to `lca_disclosures`.**
  `get_sponsorship_summary()` computes the match at call time via
  `find_matching_employers()` instead of a stored link — a company can span
  multiple legal entities in the LCA data, and match confidence varies row
  to row, so a rigid one-to-one FK would be premature normalization. Don't
  add that FK unprompted; if it's ever needed, that's a deliberate future
  decision, not a "fix" for this being "incomplete."
- **`companies.matched_sponsor_employer_name` (nullable `String(255)`,
  migration `c3be4d9c3a36`, added 2026-08-23) is a denormalized cache of
  the single top-scoring match, not a substitute for the above FK
  decision or for `get_sponsorship_summary()`'s live aggregation.**
  Populated by `scripts/resolve_sponsor_matches.py`, which calls the
  existing `find_matching_employers()` unchanged for every `companies`
  row and stores `matches[0].employer_name_normalized` (or `NULL` if
  `find_matching_employers()` returns no match above threshold — never a
  forced low-confidence guess); `sponsor_name_overrides` entries (e.g.
  Kraken → `KRAKEN TECHNOLOGIES US`) still take precedence automatically,
  since `find_matching_employers()` itself checks that table first. Not
  automatically kept fresh — re-run the script after `sponsor_name_overrides`
  changes or new LCA data is ingested. `get_sponsorship_summary()` still
  aggregates across *every* `EmployerMatch` `find_matching_employers()`
  returns (a company can span multiple legal entities), not just this one
  cached top match — don't narrow it to use this column instead, that
  would silently drop legitimate multi-entity aggregation. This step is
  persistence only: no aggregate sponsor-summary query changes and no API
  endpoint wiring yet — both are deliberately deferred to a later step.
- **A second, distinct `get_sponsorship_summary(session, company)` now
  exists at `src/huntloop/api/sponsor_summary.py` (added 2026-08-23, see
  SESSIONS.md) — same function name as
  `huntloop.matching.sponsorship.get_sponsorship_summary()`, deliberately
  a different module, different behavior. Don't merge or confuse the
  two.** This one reads only `company.matched_sponsor_employer_name`
  (Step 8's persisted column, no live `find_matching_employers()` call —
  the API-facing version is required to be fast/deterministic per
  request) and returns `None` outright if unset. It returns exactly four
  fields, matched to what `GET /jobs/{id}` needs: LCAs filed in the
  employer's most recent `fiscal_year`, median wage, the single most
  frequent job title, and `case_status` of the single most recently
  received filing — not the older function's fuller
  fiscal-year-by-fiscal-year/multi-entity aggregate. **Median wage is
  computed over `WAGE_UNIT_OF_PAY = 'Year'` rows only — checked against
  real data before deciding, not assumed (see SESSIONS.md's wage-unit
  investigation): 99.86% of the 9 matched companies' LCA rows are
  `'Year'`; the small remainder includes two rows that are unmistakably
  annual salaries mislabeled `'Week'`/`'Month'` (same Phase 1-audit
  error pattern, reconfirmed on this table) and one plausible genuine
  `'Hour'` row. Don't remove this filter or try to annualize non-`'Year'`
  rows instead — that would re-trust a unit field already shown to be
  unreliable on exactly the rows where it matters most.** `latest_case_status`
  is deliberately NOT wage-unit-filtered — it reflects whatever the
  single most recently *received* filing actually says, unit notwithstanding.
  `GET /jobs/{id}` now returns `ats_platform` (trivial `Company`
  passthrough), `sponsor` (this summary, `null` if unresolved), and
  `salary_estimate` (`{amount, basis}`, `null` whenever `sponsor` is
  `null` or has no `median_wage` — `basis` is always the fixed string
  `"Estimated from DOL wage filings for this employer, not job-specific"`,
  so this can never be mistaken for a real posted salary). `GET /jobs`
  list rows gained one lightweight boolean, `has_sponsor_history`
  (`Company.matched_sponsor_employer_name is not None`, added to the
  existing join — no per-row aggregate query, for performance). The
  frontend was NOT touched in this step — wiring this real data into the
  job detail page's sponsor sidebar (currently omitted, per the reskin
  step's known display gap) was the deliberately deferred next step.
  **That gap is now closed, 2026-08-23 (see SESSIONS.md's "Wire real
  sponsor summary/ATS/salary into the frontend" entry) — frontend-only,
  no backend/API code touched.** `frontend/src/types/api.ts`/`lib/theme.ts`
  gained the matching types (`SponsorSummary`/`SalaryEstimate`,
  `has_sponsor_history`) and a `formatWage()` helper.
  `JobDetailClient.tsx`'s sidebar gained `Salary est.`/`ATS` rows (with
  the mandatory disclaimer shown directly under them, not summarized) and
  a second "H-1B sponsorship" card ported 1:1 from the mockup's
  `sponsorCardStyle`/`sponsors`/`noSponsor` branches, including its exact
  fallback copy. `JobCard.tsx`/`JobTable.tsx` both gained the mockup's
  inline sponsor indicator next to location. Verified against the real
  running system for every real scraped company
  (checkr/duolingo/figma/palantir/wealthfront, all resolved sponsor
  matches) — the `sponsor === null` fallback render path itself was
  verified via a deliberate in-browser fetch-response override (no real
  company currently lacks a match, since Ashby/Workday spiders are
  unbuilt and kraken has 0 scraped postings), not against real
  unmatched-company data. `pytest` unaffected (58/58, unchanged).
- **`normalize_employer_name()` (`src/huntloop/matching/normalize.py`) is
  mechanical normalization only** — uppercase, strip periods/commas,
  collapse whitespace, drop a trailing legal-entity suffix
  (`INC`/`LLC`/`LLP`/`LP`/`CORP`/`CO`/`LTD`/`PLLC`/`PC`). It is not full
  entity resolution and deliberately does not merge companies beyond that
  (e.g. it won't collapse `LIMITED`-suffixed names or handle abbreviation/
  alias matching). Don't expand its suffix list or scope unprompted — see
  the false-positive-risk test in `tests/test_normalize.py` for why it
  stays conservative.
- **`find_matching_employers()` (`src/huntloop/matching/fuzzy_match.py`)
  checks `sponsor_name_overrides` before fuzzy matching, and short-circuits
  if a confirmed mapping exists.** Fuzzy matching uses `rapidfuzz`'s
  `token_set_ratio` (not `WRatio` — `WRatio`'s partial-ratio component
  scores the audit's flagged false positive, "INFOSYS" vs.
  "A&A INFOSYSTEMS", at 90, indistinguishable from a true match) at a
  default threshold of 88, chosen empirically against real
  `lca_disclosures` data (see SESSIONS.md for the concrete scores). It does
  not eliminate every collision between unrelated companies sharing a
  generic industry-suffix word (e.g. "... CONSULTANCY SERVICES",
  "... INFOSYSTEMS") — that residual ambiguity is what
  `sponsor_name_overrides` is for. `sponsor_name_overrides` has one real
  entry as of 2026-08-22 (see SESSIONS.md): `kraken` ->
  `KRAKEN TECHNOLOGIES US`, added because fuzzy matching also pulled in
  `RAKEN` (`Raken, Inc.`, an unrelated construction-software company) at
  90.9 — above the 88 threshold. The other 5 currently-scraped companies
  (`checkr`, `duolingo`, `figma`, `palantir`, `wealthfront`) each resolved
  to a single unambiguous 100.0-scoring match and needed no override.
- **Test isolation uses a throwaway Postgres schema per test session**, not
  `pytest-postgresql`. Reuses the existing local Postgres server rather than
  spinning up a separate instance. See `tests/conftest.py`.
- **`tests/conftest.py`'s isolated-schema `search_path` must NEVER include
  `public` — this caused a full production data wipe on 2026-08-22 (see
  SESSIONS.md for the full incident writeup).** Adding `,public` (to make
  the `vector` type, which lives in `public`, resolve for the new
  embedding columns) silently broke isolation: `Base.metadata.
  create_all()`'s own `has_table()` check resolves unqualified table
  names via search_path, found the *real* `public.companies`/
  `public.job_postings`/etc. before the fresh test schema had any tables
  of its own, and concluded they already existed — so it silently
  created nothing in the isolated schema, and every DB-touching test ran
  against real production data instead. `test_pipeline.py`'s teardown
  (`DELETE FROM` every table after each test) then wiped `companies`,
  `job_postings`, `job_sources`, `job_locations`, `job_skills`,
  `job_metadata`, `lca_disclosures` (1.43M rows), `sponsor_name_overrides`,
  and `resume_versions` for real. All data was restored (see SESSIONS.md
  for the exact recovery sequence and which parts were exact vs.
  necessarily inexact restores), and `alembic_version` was untouched
  throughout (`DELETE FROM` never touches it, and it isn't part of
  `Base.metadata`) — but this is exactly the failure mode to never
  reintroduce. If a future migration adds another type/extension that
  needs to resolve in DDL under this fixture, schema-qualify the type in
  its SQLAlchemy definition instead (see `huntloop.db_models.Vector`, a
  `pgvector.sqlalchemy.Vector` subclass whose `get_col_spec()` always
  emits `public.vector(n)` — confirmed via pgvector's own source that this
  only affects DDL, not value bind/result processing, so it's safe
  everywhere) — never by touching this fixture's search_path.
  **Standing check**: after touching `tests/conftest.py` or the isolated-
  schema mechanism, capture real production row counts before running the
  suite and confirm they're unchanged after — don't trust "tests green"
  alone.
- **`JobPosting.location` was dropped** in favor of the `job_locations` child
  table, which is the canonical one-to-many representation.
- **`alembic upgrade head` is the sole source of schema creation.** The
  pipeline and `test_db_insert.py` used to fall back to
  `Base.metadata.create_all()`, which could silently create tables outside
  Alembic's bookkeeping and caused a real `alembic_version` drift incident
  (see SESSIONS.md). That fallback is gone — don't re-add it. (Exception:
  `tests/conftest.py` still uses `create_all()`, but only to build tables in
  pytest's throwaway per-session schema, a separate test-isolation
  mechanism, not app schema creation.)
- **Scrapy's own logging is intentionally disabled** (`LOG_ENABLED = False`
  in `settings.py`) so `huntloop.logging_config.setup_logging()` is the only
  thing configuring the root logger — Scrapy's internal log lines (e.g.
  `scrapy.core.engine`) still show up, just formatted by our handlers
  instead of Scrapy's own. Don't re-enable `LOG_ENABLED` or set a Scrapy
  `LOG_LEVEL` — that would produce duplicate log lines (Scrapy's handler
  plus ours, both attached to root). `src/huntloop/test_db_insert.py` still
  has its own separate `logging.basicConfig()` + emoji-prefixed messages —
  intentionally left alone (it's a manual smoke-test script, not part of
  the shared-logging migration).

- **`detect_ats()` (`src/huntloop/ats_detection.py`) is static-fetch-first,
  render-as-fallback — it renders with Playwright only when the plain HTTP
  fetch matches nothing, never by default.** Rendering costs ~4-8s vs.
  <1-3s for a static hit (measured, see SESSIONS.md 2026-08-21) — an
  order of magnitude slower — so don't change this to render
  unconditionally "to be more thorough"; that trade-off is deliberate and
  documented. `playwright` in `requirements.txt` is a real, used
  dependency now (was previously unused) — the Chromium browser binary
  must be present locally (`playwright install chromium`; not yet wired
  into CI or documented in README.md, since nothing calls `detect_ats()`
  from the app yet). Even with rendering, `detect_ats()` only sees
  whatever URL it's given — it doesn't crawl a site to find the actual
  careers sub-page. `checkr.com/company/careers` (the bare marketing
  landing page) correctly stays `unknown` even after rendering, because
  that specific page never embeds the ATS board itself — it only links to
  `checkr.com/company/careers/open-careers`, which does, and which *does*
  now correctly resolve to Greenhouse via the render fallback. Workday
  tenant slugs (the `{tenant}.wd\d+.myworkdayjobs.com` subdomain) aren't
  derivable from a company name — they were found by manual probing
  during testing, not a lookup this function does or could do. Playwright
  failures (navigation timeout, browser launch failure, page crash) are
  all caught and degrade to `ats="unknown"` with `error` set — never an
  unhandled exception; a `wait_for_load_state("networkidle")` timeout
  specifically is treated as non-fatal (real sites often keep a
  tracking/analytics connection open indefinitely and never go truly
  idle even once their content has rendered).

- **`JobDataPipeline` (`src/huntloop/pipelines.py`) is genuinely
  source-agnostic — confirmed, not assumed, when the Lever spider was
  added (2026-08-21, see SESSIONS.md).** It keys off `item["name"]`
  (the spider's `self.name`, e.g. `"greenhouse_api"`/`"lever_api"`) and
  generic item fields, not anything Greenhouse-specific. The `gh_job_id`
  column name is a cosmetic wart, not a functional coupling — it's a
  plain string column that holds whatever `item["job_id"]` is for any
  source (a Greenhouse integer or a Lever UUID, both fine). Don't rename
  it unprompted; see `job_url` as the actual canonical unique key, above.
  A new spider for a new ATS platform should need zero pipeline changes,
  same as Lever did.
- **The `job_sources` duplication (`'Greenhouse'` vs. `'greenhouse_api'`)
  is root-caused and closed, not just cleaned up again — 2026-08-21, see
  SESSIONS.md.** Phase 0 Step 4 consolidated the duplicate once via a
  data-only migration, but `test_db_insert.py` kept hardcoding the
  literal `"Greenhouse"`, separate from `GreenhouseScraper.name`
  (`"greenhouse_api"`) that the real pipeline actually uses, so a manual
  smoke-test run silently recreated the duplicate. `test_db_insert.py`
  now imports and reuses `GreenhouseScraper.name` directly instead of a
  separate literal — it can't drift from the real convention again by
  construction. The live duplicate was re-consolidated (this time merging
  *into* `"greenhouse_api"`, the opposite direction from Phase 0, since
  that's now the name the fixed code will always produce) via
  `alembic/versions/3620e2fbbd47_consolidate_duplicate_greenhouse_job_.py`.
  A real regression test
  (`tests/test_db_insert_smoke_script.py`) runs the actual smoke-test
  script against pytest's isolated schema and asserts it reuses the
  pipeline's existing source row rather than creating a second one —
  confirmed to fail against the pre-fix code, not just pass against the
  fixed code. If a third spider is ever added, its own `self.name` will
  get its own distinct row the same way Lever's did automatically — no
  further pipeline or smoke-test changes needed.
- **`companies.ats_platform`/`ats_token`/`careers_url` (added 2026-08-21,
  see SESSIONS.md) are nullable and populated by running
  `scripts/detect_and_store_ats.py`** (a hardcoded 9-company curated
  list, `careers_url`-based) **and, since 2026-08-29,
  `scripts/detect_ats_for_sponsors.py`** (see below) - not automatically
  kept fresh, and not every company row has values (e.g. `OpenAI`, from
  `test_db_insert.py`'s smoke test, has all three `NULL`).
  **`scripts/detect_ats_for_sponsors.py` (2026-08-29, see SESSIONS.md)
  expanded `companies` from 9 detected rows to ~380** by taking the real
  universe of distinct `employer_name_normalized` values in
  `lca_disclosures` with >= 20 filings (8,491 employers; the full set is
  108,575 and infeasible to probe), deriving candidate board slugs from
  each name, and probing the Greenhouse
  (`boards-api.greenhouse.io/v1/boards/{slug}` + `/jobs`) and Lever
  (`api.lever.co/v0/postings/{slug}`) public APIs directly - **neither
  vendor publishes any reverse "list our customers" endpoint (confirmed
  against their own API docs)**, so slug-probing is the only option at
  scale. Result: **318 Greenhouse + 60 Lever** hits (4.5% match rate -
  an explicit LOWER BOUND, since a real board slug rarely equals a
  slugified legal name), committed as 371 new `companies` rows + 1
  updated (`brex`: `unknown`->`greenhouse`). `upsert_hits()` fills only
  NULL/`'unknown'` `ats_platform`, never overwrites a different
  successful platform. The matcher went through 3 probe passes to strip
  false positives (generic-fragment slugs like `general`/`us`/`charles`,
  Greenhouse demo tenants `linkedin`/`microsoftcorporation`, dictionary-
  word collisions `flex`/`aura`/`national`) - final gates: board-name
  fuzzy-similarity check, a dictionary-word stoplist + acronym rule,
  >= 3 postings, "test"/"demo" name rejection. Residual FP risk remains
  on generic 3-letter slugs at the low-filing tail (`rpa`, `pmg`,
  `grey`) - accepted as MVP noise. **Re-run
  `scripts/detect_ats_for_sponsors.py` after each new quarterly DOL LCA
  file is ingested** (`scripts/ingest_lca_disclosures.py`), NOT on a
  fixed calendar - a new quarter adds employers and pushes others past
  the 20-filing threshold; safe to re-run (only inserts new / fills
  NULL-or-unknown). `main.py` needs no changes - it already groups
  `companies` by `ats_platform`. No new spiders were built; `ashby`/
  `workday`/`neither` employers are still skipped. **Verified with a
  real `docker compose run app python main.py`: `job_postings` 649 ->
  30,363 (+29,714), every new row's `is_relevant` populated at insert
  time (0 NULL at 30k scale).** One pre-existing bug surfaced at this
  scale (not fixed - 0.12% of rows, gracefully handled): a long
  semicolon-joined multi-location string overflows
  `job_locations.location_name` `varchar(255)`; the pipeline catches
  the `DataError` and skips that item. Company rows use the same lowercase-token naming convention as
  spider-created rows (`"checkr"`, not `"Checkr"`) specifically to avoid
  repeating the `job_sources` naming-drift bug above for `companies`.
  **`upsert_company_ats()` will not overwrite an existing, previously-
  successful `ats_platform`/`ats_token` value when the current
  `detect_ats()` result has `error` set** (2026-08-21 follow-up fix, see
  SESSIONS.md - a real transient `ReadTimeout` was observed silently
  blanking a correct `checkr` detection back to `unknown`/`NULL` before
  this) - it logs a `"detection attempt failed"` warning and leaves the
  stored value alone instead. A genuine error-free "checked, nothing
  matched" result still overwrites to `"unknown"` normally; a company
  with no prior successful value still stores `unknown`/`NULL` on error,
  since there's nothing to protect. Don't revert this to unconditional
  overwrite - `tests/test_detect_and_store_ats.py` would catch it (proven
  to fail against the old behavior).
- **`GreenhouseScraper`/`LeverScraper` both accept a `companies`
  constructor/spider argument, consistently (2026-08-21, see
  SESSIONS.md).** `__init__(self, companies=None, *args, **kwargs)` on
  both: `None` falls back to the original single-company default
  (`['checkr']`/`['wealthfront']`) for backward compat; a string is
  split on commas (the shape `-a companies=...` would arrive as, if
  `scrapy crawl` were ever wired up); a list/tuple is used directly (what
  `main.py`'s orchestrator passes). Keep any future spider's constructor
  consistent with this shape rather than inventing a different one.
- **`main.py` is the multi-ATS orchestrator, not just "the Greenhouse
  entrypoint" anymore (2026-08-21, see SESSIONS.md).**
  `get_companies_by_platform()` queries `companies` for non-NULL
  `ats_platform` rows (grouped by platform) plus a separate NULL-platform
  query (logged, not silently excluded). `run_multi_ats_scrape()` routes
  each platform through `SPIDERS_BY_PLATFORM` (`{"greenhouse":
  GreenhouseScraper, "lever": LeverScraper}`) - one `process.crawl()` call
  per platform with its full token list, not one call per company. A
  platform missing from that dict (`"ashby"`, `"workday"`, and the
  literal string `"unknown"` all hit the same lookup-miss path - no
  special-casing needed) gets a `logger.warning()` and is skipped, never
  a crash or a silent drop. When adding a new spider, add its platform
  string as a key here - that's the only wiring required.

## How to run things

See README.md for full detail (setup, running the scraper, migrations, tests).
Short version: `python main.py` (scraper), `pytest` (tests), `alembic upgrade
head` (migrations).

## Current phase / what's next

Phase 0 (repo hygiene / foundations) is done: env config extraction, Alembic
setup, schema-drift reconciliation, small bug fixes, pytest scaffold,
README, Docker (app + Postgres via docker-compose), and a minimal CI
workflow (migrations + pytest against a real Postgres service on every
push/PR).

Phase 1 (sponsorship-matching MVP) core is now done, as of 2026-08-20:
DOL LCA disclosure files were audited and ingested (11 fiscal-year/quarter
files, 1,431,321 rows in `lca_disclosures`, via
`scripts/ingest_lca_disclosures.py`); every row has a mechanically
normalized `employer_name_normalized` (`normalize_employer_name()`, 108,575
distinct values vs. 129,295 distinct raw `employer_name` values); fuzzy
matching (`find_matching_employers()`, `rapidfuzz`-based, with a
`sponsor_name_overrides` manual-correction escape hatch) resolves a raw
company name to likely `employer_name_normalized` candidates; and
`get_sponsorship_summary()` applies that to a real `Company` row and
returns an aggregated sponsorship picture (total approved LCAs by fiscal
year, distinct job titles, distinct worksite states). Verified end-to-end
against both currently-scraped companies at the time (`checkr`: 48
approved LCAs 2021-2025; `duolingo`: 95 approved LCAs 2021-2025) — both
matches manually confirmed correct, no override needed. Extended
2026-08-22 (see SESSIONS.md) to the 4 companies scraped since Phase 2 —
`figma` (107 approved LCAs), `palantir` (241), `wealthfront` (43) all
resolved to a single unambiguous match each; `kraken` (1 approved LCA)
needed `sponsor_name_overrides`' first real entry to exclude an unrelated
company (`Raken, Inc.`) that fuzzy-matched above threshold. Shared logging
(`src/huntloop/logging_config.py`, `LOG_LEVEL`-controlled, console +
rotating file) is also in place across the scraper, pipeline, and scripts.
See SESSIONS.md for the full log.

A security audit (2026-08-21, see SESSIONS.md) found the repo clean on
`pip-audit`/`bandit`/`gitleaks` (including full git history — the
pre-Phase-0 hardcoded Postgres password never actually entered git
history). The two medium-severity findings (Docker running as root,
`data/raw/`/`logs/` missing from `.dockerignore`) are fixed — see the
Docker bullet above. Left open by deliberate choice, not oversight:
`requirements.txt` is mostly unpinned (3 of 18 direct deps have an `==`
pin), `.idea/` is tracked in git despite being in `.gitignore` (committed
before the ignore rule existed), and there's no automated
`pip-audit`/`bandit` step in CI. Don't "fix" these unprompted — they're
tracked follow-ups, not bugs.

A standalone ATS-detection function (`detect_ats()`,
`src/huntloop/ats_detection.py`, 2026-08-21, see SESSIONS.md) also exists
now: given a company's careers URL, it identifies Greenhouse/Lever/
Ashby/Workday/SmartRecruiters from static HTML first, falling back to a
Playwright-rendered fetch (added same day, see SESSIONS.md) only when the
static fetch matches nothing, or `unknown` if neither does. Manually
verified against the same 13 real, live URLs from the first pass plus the
`checkr.com/company/careers/open-careers` case the render fallback was
built to fix — 12/13 now correctly detected (up from 11/13 static-only);
the 13th (`checkr.com/company/careers`, the bare landing page) correctly
stays `unknown` even after rendering, because that exact page never
embeds the ATS board itself (see the architectural decisions above for
why that's not a bug). Not wired into the scraper, `company_tokens`, or
any pipeline — detection only, standalone.

**Phase 2 (multi-ATS scraping) core is now done, as of 2026-08-21** — the
full loop from detection to real scraped data works end-to-end:

- `LeverScraper` (`src/huntloop/spiders/lever_spider.py`) exists
  alongside `GreenhouseScraper`, mirroring its structure and
  `custom_settings` exactly. `JobDataPipeline` needed zero changes for
  it — confirmed genuinely source-agnostic (see the architectural
  decisions above). The `job_sources` naming duplication this first
  surfaced (`'Greenhouse'` vs. `'greenhouse_api'`) was root-caused and
  closed the same day — `job_sources` now has exactly one row per real
  source.
- `companies.ats_platform`/`ats_token`/`careers_url` columns exist,
  populated via `scripts/detect_and_store_ats.py` for a 9-company
  curated list spanning all 5 platforms `detect_ats()` recognizes (2
  never tested against it before that script — both verified correct
  independently). A same-day follow-up fixed a transient-failure
  overwrite bug this surfaced (see the architectural decisions above).
- Both spiders now accept a `companies` list instead of one hardcoded
  token, and `main.py` is a real orchestrator: it queries
  `companies.ats_platform`, routes `greenhouse`/`lever` companies to one
  `process.crawl()` call each with their full token list, and skips
  `ashby`/`workday`/`unknown`/NULL companies with a clear log message
  (see the architectural decisions above). Verified end-to-end for real
  (`python main.py`, no mocking): `job_postings` 136 -> 616 (+480) across
  6 companies with implemented spiders (`checkr`/`duolingo`/`figma` via
  Greenhouse, `kraken`/`palantir`/`wealthfront` via Lever); `ramp`
  (ashby), `adobe` (workday), `brex` (unknown), and `OpenAI` (NULL) all
  correctly skipped with distinct log messages, not silently dropped.
  `figma` and `palantir` (never scraped before this run) spot-checked
  against the DB and live pages — correct. `kraken` legitimately scraped
  0 jobs (its real Lever board has 0 open postings right now, confirmed
  live — not a detection or spider bug).

Not yet started / explicitly deferred: Ashby/Workday spiders (`ramp`,
`adobe` will keep getting skipped until one exists — deliberately out of
scope, future work once there's demand), broadening the curated company
list beyond the current 9, automating the
`detect_and_store_ats.py` -> `main.py` sequence (currently two separate
manual steps), broader scraper coverage generally, any UI/API surface for
`get_sponsorship_summary()` (it's a Python function today, called
directly, not exposed via an endpoint or the scraper pipeline), curating
`sponsor_name_overrides` for companies fuzzy matching doesn't resolve
cleanly (table is still empty), broader test coverage, scraper
parsing/HTTP tests, CI linting/build/deploy steps, any FK from
`companies` to `lca_disclosures` (deliberately not built — see the
architectural decisions above), and an LLM-extraction fallback for career
pages that resist both static fetch and rendering. Nothing beyond what's
listed above should be assumed built.

**Since the above (see SESSIONS.md for full detail on each), a full
sponsorship-matching + API + frontend stack was built on top of Phase 2's
scraping foundation, and the Frontend/UI MVP is now closed out
(2026-08-23):** DOL LCA disclosures ingested (1,431,321 rows) and fuzzy-
matched to companies (`find_matching_employers()`/
`get_sponsorship_summary()`); embedding-based resume-to-job match scoring
(pgvector, `all-MiniLM-L6-v2`) with a Groq-based skills-matching engine
(matched/missing skills per job) and a daily-cron-integrated sanity
filter; a FastAPI backend (`src/huntloop/api/`) exposing `GET /jobs`,
`GET /jobs/{id}`, and `PATCH /jobs/{id}/application` over real Postgres
data, with `job_applications` (status/`applied_at`/`status_updated_at`/
`notes`) as a real upserted-not-history table; and a Next.js frontend
(`frontend/`, App Router + TanStack Query + Tailwind) rendering the real
job list with company/min-score filtering, sorting, and pagination, a
calibrated score indicator, matched/missing skill chips, and — as of this
entry — an interactive `StatusControl` on each job card wired to the real
`PATCH` endpoint via a TanStack Query mutation with optimistic updates,
rollback-on-failure, and toast feedback, fully verified against the real
running system (see SESSIONS.md's 2026-08-23 "Interactive status updates
on job cards" entry for the exact screenshots/psql evidence). Prometheus/
Grafana observability and local cron scheduling are also in place,
layered on the scraper.

**The frontend was reskinned 2026-08-23 (see SESSIONS.md's "Frontend
reskin against the Claude Design mockup" entry) against
`design/HuntLoop.dc.html`** — a Claude Design mockup in x-dc/sc-for/sc-if
runtime format; read it as the visual/layout spec (colors ported into
`frontend/src/lib/theme.ts`, typography via `next/font/google`'s
JetBrains Mono, Tailwind theme tokens in `globals.css`), not as literal
code. **Two screens that didn't exist before this reskin were built as
part of it**, reusing only the existing three API endpoints: a job detail
page (`frontend/src/app/jobs/[id]/`, matched skills shown first in green,
missing second in dashed muted styling) and an applications tracker
(`frontend/src/app/applications/`, kanban board with native HTML5
drag-and-drop + a list view, both driving the same `PATCH` mutation now
shared via `frontend/src/hooks/useApplicationStatus.ts` instead of living
only in `StatusControl`). The job list gained a cards/table view toggle.
Per-job H-1B sponsor status, ATS platform, and salary estimate were shown
in the mockup but not exposed by the real API at reskin time — since
closed (2026-08-23, see the sponsor-summary entries above and
SESSIONS.md): the API now returns all three and the job detail
page/sponsor sidebar/list-view sponsor indicator all consume them for
real. AI resume-review screens and location-radius/department filters
remain explicitly out of scope per that task's own instructions.
**Dashboard is also no longer out of scope** — `frontend/src/app/
dashboard/page.tsx` (added 2026-08-23, see SESSIONS.md's "Real dashboard
page" entry) is real, wired to `GET /dashboard/stats`, and is the app's
home view (`/` redirects there — see the routing note above). **Neither
is Resume version management** — `frontend/src/app/resumes/page.tsx`
(added 2026-08-24, see SESSIONS.md's "Real resume management page"
entry) is real, wired to Step 13's `GET /resumes`/`POST /resumes/
upload`/`PATCH /resumes/{id}/activate`, and is reachable via `NavBar`'s
fourth tab. This reskin (plus the sponsor-data, dashboard, and resume-
management follow-ups) is now the current state of the frontend — treat
everything above this note (Phase 0 repo hygiene through the
ATS-detection standalone function) as historical foundation, not the
latest picture. Not yet started: the AI resume-review UI (missing
keywords/phrasing suggestions/formatting notes — no backend for it
exists yet), Ashby/Workday spiders, and everything else already listed
as deferred above — those deferrals still stand.
