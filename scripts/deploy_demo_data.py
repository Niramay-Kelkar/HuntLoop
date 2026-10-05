"""
Loads (or refreshes) the public demo snapshot on a remote target
database, for the Vercel/Render/Neon demo deployment described in
docs/DEPLOY_DEMO.md.

This is a thin orchestration wrapper around two things that already
exist and are unchanged by this script:
  - `alembic upgrade head`, run against TARGET_DATABASE_URL, which
    creates the schema and the pgvector extension (migration
    c2d25907fe8e's `CREATE EXTENSION IF NOT EXISTS vector;`).
  - scripts/build_demo_dataset.py, run with --force so this script is
    safe to rerun whenever the operator wants a fresh snapshot (its own
    idempotent-skip-if-populated default would otherwise mean a second
    run does nothing).

Requires SOURCE_DATABASE_URL and TARGET_DATABASE_URL in the environment
- both read fresh from os.environ, same convention as
build_demo_dataset.py, never a fallback to DATABASE_URL or any value
typed into this script. Neither is ever printed or logged - only the
host/port/database portion (never user/password) is shown in any
message, for the same reason build_demo_dataset.py's own messages do
this.

Two refusal checks, enforced before anything else runs:
  - target and source must not resolve to the same host/port/database
    (same check build_demo_dataset.py already does on its own, run
    again here so this script fails fast before even attempting
    migrations).
  - target must not be localhost/127.0.0.1 on port 5432 - this
    project's real production Postgres (see CLAUDE.md's "two distinct
    local Postgres instances" note). This script must never run
    migrations or write demo data into production, and this is the one
    automatic check available for that.

Must work against a remote managed Postgres requiring SSL (Neon): both
alembic and build_demo_dataset.py connect using the URL exactly as
given, so an `sslmode=require` (or Neon's own recommended connection
string shape) in TARGET_DATABASE_URL is passed straight through to
psycopg2/libpq with no modification by this script.

Run (see docs/DEPLOY_DEMO.md for the full walkthrough):

    SOURCE_DATABASE_URL="postgresql+psycopg2://...@host.docker.internal:5432/jobsight" \\
    TARGET_DATABASE_URL="postgresql+psycopg2://...neon.tech/jobsight?sslmode=require" \\
    docker compose run --rm \\
      -e SOURCE_DATABASE_URL -e TARGET_DATABASE_URL \\
      app python scripts/deploy_demo_data.py

(build_demo_dataset.py needs torch/sentence-transformers for the demo
resume's embedding - same app-Docker-image requirement its own
docstring already documents - this wrapper inherits that requirement
rather than changing it.)
"""
import logging
import os
import subprocess
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "src"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("deploy_demo_data")

_PRODUCTION_HOSTS = {"localhost", "127.0.0.1"}
_PRODUCTION_PORT = 5432

_DEMO_TABLES = (
    "companies",
    "job_sources",
    "job_postings",
    "job_locations",
    "job_metadata",
    "job_applications",
    "resume_versions",
    "sponsor_fiscal_year_aggregates",
    "sponsor_overall_aggregates",
    "demo_meta",
    "company_research",
)


def _identity(url_str: str):
    url = make_url(url_str)
    return (url.host, url.port, url.database)


def _refuse_if_same_database(source_url: str, target_url: str) -> None:
    source_identity = _identity(source_url)
    target_identity = _identity(target_url)
    if source_identity == target_identity:
        raise SystemExit(
            f"SOURCE_DATABASE_URL and TARGET_DATABASE_URL both resolve to {target_identity} - "
            f"refusing to run. This script must never write to the database it reads from."
        )


def _refuse_if_target_is_production(target_url: str) -> None:
    host, port, _database = _identity(target_url)
    if host in _PRODUCTION_HOSTS and port == _PRODUCTION_PORT:
        raise SystemExit(
            f"TARGET_DATABASE_URL resolves to {host}:{port} - this is the local production "
            f"Postgres convention for this project (see CLAUDE.md). Refusing to run migrations "
            f"or demo data against it. TARGET_DATABASE_URL must point at the demo database "
            f"(the Neon project), never at production."
        )


def _run_migrations(target_url: str) -> None:
    logger.info("deploy_demo_data: running alembic upgrade head against the target database...")
    env = dict(os.environ)
    env["DATABASE_URL"] = target_url
    result = subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=REPO_ROOT,
        env=env,
    )
    if result.returncode != 0:
        raise SystemExit(f"alembic upgrade head failed (exit code {result.returncode}).")
    logger.info("deploy_demo_data: migrations applied (schema + pgvector extension).")


def _run_build_demo_dataset() -> None:
    logger.info("deploy_demo_data: running scripts/build_demo_dataset.py --force...")
    result = subprocess.run(
        [sys.executable, os.path.join(REPO_ROOT, "scripts", "build_demo_dataset.py"), "--force"],
        cwd=REPO_ROOT,
        env=dict(os.environ),
    )
    if result.returncode != 0:
        raise SystemExit(f"build_demo_dataset.py failed (exit code {result.returncode}).")


def _report(target_url: str) -> None:
    host, port, database = _identity(target_url)
    logger.info(f"deploy_demo_data: reporting row counts and database size for {host}:{port}/{database}")
    engine = create_engine(target_url)
    with engine.connect() as conn:
        for table in _DEMO_TABLES:
            count = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
            logger.info(f"  {table}: {count} row(s)")
        size = conn.execute(text("SELECT pg_size_pretty(pg_database_size(current_database()))")).scalar_one()
        logger.info(f"  database size: {size}")
    engine.dispose()


def main() -> None:
    source_url = os.environ.get("SOURCE_DATABASE_URL")
    target_url = os.environ.get("TARGET_DATABASE_URL")
    if not source_url or not target_url:
        raise SystemExit("SOURCE_DATABASE_URL and TARGET_DATABASE_URL must both be set.")

    _refuse_if_same_database(source_url, target_url)
    _refuse_if_target_is_production(target_url)

    _run_migrations(target_url)
    _run_build_demo_dataset()
    _report(target_url)

    logger.info("deploy_demo_data: done.")


if __name__ == "__main__":
    main()
