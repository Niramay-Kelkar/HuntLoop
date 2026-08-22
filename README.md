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

The orchestrator (`main.py`) can run on a schedule via `cron`, so postings
stay fresh without a manual `python main.py` each time. This is local-only
automation for a dev machine that's actually on/awake at the scheduled
time — it is not a substitute for real deployment scheduling (e.g. GitHub
Actions against a hosted Postgres), which is a separate, later step once
there's a publicly reachable database.

`scripts/run_orchestrator_cron.sh` is the entrypoint cron calls. It's a
thin wrapper, not a parallel code path: it `cd`s into the repo (cron's
working directory isn't otherwise predictable), then runs
`.venv/bin/python main.py` — the exact same script, same `.venv`, same
`.env`/`DATABASE_URL` a manual run uses. Nothing about credentials or
config is duplicated for the scheduled path.

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
  start/end pair per run, with a timestamp and the exit code, e.g.:
  ```
  === HuntLoop scheduled orchestrator run started 2026-08-22 03:00:01 PDT ===
  ...
  === run finished with exit code 0 at 2026-08-22 03:00:47 PDT ===
  ```
  A non-zero exit code (or an `ERROR: .venv/bin/python not found` line,
  if the venv itself is missing) means the run failed. Check this file
  first — it's the fastest way to answer "did last night's run happen,
  and did it succeed."
- `logs/huntloop.log` — the same rotating, shared-logging file every
  manual run also writes to (see "Logging" above; 5MB cap, 3 backups).
  Scheduled runs show up in it exactly like manual ones — same format,
  same per-company INFO/WARNING lines, same Scrapy stats dump at the end.
  This is where to look for *what* happened during a run (which
  companies were scraped, which were skipped and why, item counts),
  not just whether it succeeded.

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
