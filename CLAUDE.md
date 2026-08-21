# CLAUDE.md

Context for Claude Code sessions working in this repo. Keep this short — it's a
reference for fast orientation, not documentation. Full setup/usage detail lives
in README.md.

## Project overview

HuntLoop, today, is a Greenhouse job-board scraper (Scrapy) that pipes postings
into a Postgres database via SQLAlchemy. That's the whole built system: scrape
Greenhouse's public API → normalize into `JobPostingItem` → `JobDataPipeline`
upserts companies/sources/postings/locations/skills.

Broader vision (not yet built): aggregate job postings across many sources, and
add sponsorship-aware matching so candidates can filter for companies that
actually sponsor visas (e.g. H1B). Treat anything beyond the Greenhouse pipeline
above as planned, not present.

## Tech stack and conventions

- Package root: `huntloop` lives under `src/` (`src/huntloop/...`). Anything
  importing it needs `src/` on `sys.path` (see `main.py`, `pytest.ini`).
- Config: `.env` (gitignored) + `python-dotenv`, loaded in `settings.py`. Never
  hardcode credentials — `.env.example` documents the required shape.
- Migrations: Alembic, config at repo root (`alembic.ini`, `alembic/`).
- Tests: pytest, config at repo root (`pytest.ini`), tests live in `tests/`.
- Entrypoint: `python main.py` runs the Greenhouse scraper end-to-end.
- Docker: `Dockerfile` + `docker-compose.yml` (app + postgres:18) for a
  dev-oriented containerized setup. Not hardened for production. CI
  (`.github/workflows/ci.yml`) runs migrations + pytest against a real
  Postgres service container on every push/PR to `master`.
- One-off scripts live in `scripts/` (not part of the ongoing app pipeline
  or CI) — e.g. `scripts/ingest_lca_disclosures.py`, run manually. Uses
  `pandas`/`openpyxl` (in `requirements.txt`) to read DOL's `.xlsx`
  disclosure files from `data/raw/dol_lca/` (gitignored — see SESSIONS.md's
  Step 1 audit for how those files are obtained; they're downloaded
  manually, not fetched by any code in this repo).

## Key architectural decisions (already made — don't re-litigate)

- **`job_url` is the canonical unique key** on `job_postings`, not `gh_job_id`.
  `gh_job_id` isn't reliably unique and won't generalize to non-Greenhouse
  sources later.
- **`Company.h1b_sponsorship` is intentionally kept** even though it's a bare
  boolean today. It will likely be superseded by real sponsorship data —
  `LcaDisclosure` (`lca_disclosures` table) now holds raw DOL LCA disclosure
  records, but it's standalone with no FK to `companies` yet; that link,
  plus `employer_name_normalized` population, is a later step (see
  SESSIONS.md). Don't "fix" `h1b_sponsorship` in the meantime by removing or
  redesigning it unprompted.
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

## How to run things

See README.md for full detail (setup, running the scraper, migrations, tests).
Short version: `python main.py` (scraper), `pytest` (tests), `alembic upgrade
head` (migrations).

## Current phase / what's next

Phase 0 (repo hygiene / foundations) is done: env config extraction, Alembic
setup, schema-drift reconciliation, small bug fixes, pytest scaffold,
README, Docker (app + Postgres via docker-compose), and a minimal CI
workflow (migrations + pytest against a real Postgres service on every
push/PR). Sponsorship-matching work is underway: DOL LCA disclosure sample
files were audited (real schema, messy employer-name formatting, case
status values), the standalone `lca_disclosures` table was added, and
`scripts/ingest_lca_disclosures.py` now ingests every downloaded modern-
format quarter (not just one). As of 2026-08-20, 11 fiscal-year/quarter
files are loaded — FY2021 Q1 & Q4, FY2022 Q4, FY2024 Q1-Q4, FY2025 Q1-Q4 —
1,431,321 total rows in `lca_disclosures`. See SESSIONS.md for the full
log. Not yet started: broader test coverage, scraper parsing/HTTP tests,
CI linting/build/deploy steps, multi-source aggregation, employer-name
normalization/matching, or the FK from `lca_disclosures` to `companies` —
nothing beyond what's listed above should be assumed built.
