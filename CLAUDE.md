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
- Scheduling: `scripts/run_orchestrator_cron.sh` + a local crontab entry
  (`0 3 * * *`, daily) run `main.py` unattended — added 2026-08-22, see
  SESSIONS.md. The wrapper is a thin `cd` + `.venv/bin/python main.py`
  call; it does not duplicate `.env`/`DATABASE_URL` or point at a
  different DB than manual runs. `logs/cron.log` (new, also gitignored
  under `logs/`) is a lightweight start/exit-code/end marker log,
  separate from and in addition to the existing rotating
  `logs/huntloop.log` — see README's "Scheduled runs" section for
  enable/disable/where-to-check. This is local-only automation; GitHub
  Actions scheduling against a hosted Postgres (Supabase/Neon) is a
  deliberately separate, later deployment step — don't build it
  unprompted.
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
  active.
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
  crash, not a hang). **Skills-matching is now a stage of the daily cron
  orchestrator (`scripts/run_orchestrator_cron.sh`, same `0 3 * * *`
  crontab entry as Step 5/7 — no separate schedule)** — stage 1 is the
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
  see SESSIONS.md) are nullable and populated only by manually running
  `scripts/detect_and_store_ats.py`** against a hardcoded curated list -
  not automatically kept fresh, and not every company row has values yet
  (e.g. `OpenAI`, from `test_db_insert.py`'s smoke test, has all three
  `NULL`). Company rows use the same lowercase-token naming convention as
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
