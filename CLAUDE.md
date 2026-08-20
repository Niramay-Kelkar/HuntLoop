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

## Key architectural decisions (already made — don't re-litigate)

- **`job_url` is the canonical unique key** on `job_postings`, not `gh_job_id`.
  `gh_job_id` isn't reliably unique and won't generalize to non-Greenhouse
  sources later.
- **`Company.h1b_sponsorship` is intentionally kept** even though it's a bare
  boolean today. It will likely be superseded by a dedicated sponsors table
  once real sponsorship data is modeled properly — don't "fix" it in the
  meantime by removing or redesigning it unprompted.
- **Test isolation uses a throwaway Postgres schema per test session**, not
  `pytest-postgresql`. Reuses the existing local Postgres server rather than
  spinning up a separate instance. See `tests/conftest.py`.
- **`JobPosting.location` was dropped** in favor of the `job_locations` child
  table, which is the canonical one-to-many representation.

## How to run things

See README.md for full detail (setup, running the scraper, migrations, tests).
Short version: `python main.py` (scraper), `pytest` (tests), `alembic upgrade
head` (migrations).

## Current phase / what's next

Phase 0 (repo hygiene / foundations) is done: env config extraction, Alembic
setup, schema-drift reconciliation, small bug fixes, pytest scaffold,
README, Docker (app + Postgres via docker-compose), and a minimal CI
workflow (migrations + pytest against a real Postgres service on every
push/PR). See SESSIONS.md for the log. Not yet started: broader test
coverage, scraper parsing/HTTP tests, CI linting/build/deploy steps,
multi-source aggregation, sponsorship matching — nothing beyond what's
listed above should be assumed built.
