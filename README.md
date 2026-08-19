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
2026-08-19 12:55:04 [huntloop.pipelines] WARNING: 🚀 [PIPELINE TRIGGERED] Processing item: Chief of Staff
2026-08-19 12:55:04 [huntloop.pipelines] INFO: 🟡 Skipping reposted job 7913718
```

(`Skipping reposted job` means that `gh_job_id` was already in the database —
expected on any run after the first. On a genuinely new posting you'll instead
see `✅ Inserted job: <title> for <company>`.) The run ends with Scrapy's stats
dump (`item_scraped_count`, `finish_reason: finished`, etc.) and:

```
2026-08-19 12:54:46 [huntloop.pipelines] INFO: 🛑 JobDataPipeline closed
2026-08-19 12:54:46 [scrapy.core.engine] INFO: Spider closed (finished)
```

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

**Not built yet:**
- Scraping more than one hardcoded company, or sources other than Greenhouse
- Any job matching or sponsorship-based filtering (the `h1b_sponsorship`
  column exists on `companies` but nothing populates or reads it yet)
- An API or any user-facing interface
- Deployment/containerization of any kind
