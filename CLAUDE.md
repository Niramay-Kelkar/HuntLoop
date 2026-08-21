# CLAUDE.md

Context for Claude Code sessions working in this repo. Keep this short — it's a
reference for fast orientation, not documentation. Full setup/usage detail lives
in README.md.

## Project overview

HuntLoop, today, is a Greenhouse + Lever job-board scraper (Scrapy) that pipes
postings into a Postgres database via SQLAlchemy. That's the whole built
system: scrape each platform's public JSON API → normalize into
`JobPostingItem` → the single, source-agnostic `JobDataPipeline` upserts
companies/sources/postings/locations/skills. `main.py` (the actual
entrypoint) still only runs the Greenhouse spider — the Lever spider exists
and is proven standalone (`src/huntloop/spiders/lever_spider.py`, see
SESSIONS.md 2026-08-21) but isn't wired into `main.py` yet.

Broader vision (not yet built): aggregate job postings across many sources, and
add sponsorship-aware matching so candidates can filter for companies that
actually sponsor visas (e.g. H1B). Treat anything beyond the Greenhouse pipeline
above as planned, not present. One standalone building block toward
multi-source aggregation exists today: `detect_ats()`
(`src/huntloop/ats_detection.py`), which identifies which ATS platform a
company's careers page uses — see the architectural decisions below. It is
not wired into the scraper or any company list; nothing calls it yet.

## Tech stack and conventions

- Package root: `huntloop` lives under `src/` (`src/huntloop/...`). Anything
  importing it needs `src/` on `sys.path` (see `main.py`, `pytest.ini`).
- Config: `.env` (gitignored) + `python-dotenv`, loaded in `settings.py`. Never
  hardcode credentials — `.env.example` documents the required shape.
- Migrations: Alembic, config at repo root (`alembic.ini`, `alembic/`).
- Tests: pytest, config at repo root (`pytest.ini`), tests live in `tests/`.
- Entrypoint: `python main.py` runs the Greenhouse scraper end-to-end.
- Docker: `Dockerfile` + `docker-compose.yml` (app + postgres:18) for a
  dev-oriented containerized setup. CI (`.github/workflows/ci.yml`) runs
  migrations + pytest against a real Postgres service container on every
  push/PR to `master`. The app container runs as a dedicated non-root
  `huntloop` user (not root) — see the security audit entry in
  SESSIONS.md (2026-08-21). `.dockerignore` excludes `data/raw/` and
  `logs/` (mirroring `.gitignore`) so real LCA data and log output never
  get baked into an image layer.
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
  `sponsor_name_overrides` is for. `sponsor_name_overrides` is still empty
  — for both companies checked so far (`checkr`, `duolingo`), fuzzy
  matching alone was already unambiguous and verified correct, so no
  override was needed yet; don't assume the table has real entries in it.
- **Test isolation uses a throwaway Postgres schema per test session**, not
  `pytest-postgresql`. Reuses the existing local Postgres server rather than
  spinning up a separate instance. See `tests/conftest.py`.
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
against both currently-scraped companies (`checkr`: 48 approved LCAs
2021-2025; `duolingo`: 95 approved LCAs 2021-2025) — both matches manually
confirmed correct, no override needed. Shared logging
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

A second working spider, `LeverScraper`
(`src/huntloop/spiders/lever_spider.py`, 2026-08-21, see SESSIONS.md),
now exists alongside `GreenhouseScraper`, mirroring its structure and
`custom_settings` exactly. Hardcoded to one company (`wealthfront`, a
confirmed real Lever user from the ATS-detection step). Verified
end-to-end against the real Lever API: 22 jobs scraped and inserted
(`job_postings` 114 -> 136), 3 spot-checked against live Lever job pages
(title, URL, and multi-location mapping all correct), and `job_sources`
confirmed to have gotten its own distinct `'lever_api'` row rather than
collapsing into either existing Greenhouse row. `JobDataPipeline` needed
zero changes — confirmed genuinely source-agnostic (see the architectural
decision above). Not wired into `main.py`, `detect_ats()`'s output, or
any multi-company config yet — standalone, per the task. The
`job_sources` naming duplication this surfaced was root-caused and closed
the same day (see the architectural decisions above) — `job_sources` now
has exactly one row per real source (`greenhouse_api`, `lever_api`).

Not yet started / explicitly deferred: broader scraper coverage (only
`checkr` and `duolingo` are configured for Greenhouse, `wealthfront` for
Lever), a company-list config that routes a company to the right spider
based on `detect_ats()`'s output (the natural next step now that two
spiders are proven), wiring the Lever spider into `main.py`, any UI/API
surface for `get_sponsorship_summary()` (it's a Python function today,
called directly, not exposed via an endpoint or the scraper pipeline),
curating `sponsor_name_overrides` for companies fuzzy matching doesn't
resolve cleanly (table is still empty), broader test coverage, scraper
parsing/HTTP tests, CI linting/build/deploy steps, and any FK from
`companies` to `lca_disclosures` (deliberately not built — see the
architectural decisions above), and an LLM-extraction fallback for career
pages that resist both static fetch and rendering. Nothing beyond what's
listed above should be assumed built.
