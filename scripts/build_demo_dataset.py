"""
Builds a small, safe demo dataset for DEMO_MODE (see huntloop.demo_mode,
CLAUDE.md) and loads it into a target database.

Reads from SOURCE_DATABASE_URL and writes to TARGET_DATABASE_URL, both
environment variables - this script never falls back to DATABASE_URL or
any other default, so there is no way to accidentally point either side
at a connection string typed in by hand elsewhere. The source connection
is opened read only at the Postgres session level
(default_transaction_read_only=on) in addition to this script simply
never issuing a write against it, and the script refuses outright if
source and target resolve to the same host, port and database name.

What gets copied, and what does not:
  - Essentially every company with at least one relevant, already-
    embedded posting (is_relevant IS TRUE AND embedding IS NOT NULL) on
    any of the 7 ATS platforms - not a fixed total-postings sample
    capped at a round number. Each company's postings are capped at
    PER_COMPANY_CAP, preferring postings with a US location, so a
    handful of very large boards (Workday, iCIMS) don't crowd out
    coverage of the many smaller ones - but every eligible company
    itself is included, since company-count coverage (not postings
    volume) is what the demo is actually selling. Their matching
    companies, job_sources, job_locations and job_metadata rows come
    with them.
  - Sponsorship data is NOT copied as raw lca_disclosures rows. Instead
    this builds sponsor_fiscal_year_aggregates (a per fiscal year
    breakdown: total filings, distinct titles, median wage) and
    sponsor_overall_aggregates (the single summary
    huntloop.api.sponsor_summary.get_sponsorship_summary() needs in
    demo mode) for exactly the sampled companies that have a resolved
    sponsor match. The overall aggregate is built by calling the real
    production aggregation function
    (huntloop.api.sponsor_summary._get_sponsorship_summary_from_lca)
    read-only against the source database, so the numbers match what
    production itself would show for the same company.
  - A single fictional resume, for an invented person, written from
    scratch for this script - it shares no text with any real resume.
    It is run through the real extraction-adjacent cleaning
    (huntloop.text_cleaning) and the real embedding model
    (huntloop.embeddings.embed_text), so its embedding and therefore
    every match score in the demo are computed the real way, not faked.
    This needs a working torch/sentence-transformers install (see
    huntloop.embeddings' own docstring) - run this script inside the
    app Docker image, the same way scripts/backfill_embeddings.py does:

        docker compose run --rm \\
          -e SOURCE_DATABASE_URL="postgresql+psycopg2://<user>:<password>@host.docker.internal:5432/jobsight" \\
          -e TARGET_DATABASE_URL="postgresql+psycopg2://<user>:<password>@host.docker.internal:5433/jobsight" \\
          app python scripts/build_demo_dataset.py

  - resume_versions and job_applications are never read from the
    source at all - not filtered, not read and discarded, simply never
    queried. The only resume_versions row this script ever writes is
    the fictional one it writes itself, and job_applications is left
    empty on the target.
  - A demo_meta row recording the snapshot date, read by GET
    /demo-info (huntloop.api.routers.demo_info) for the frontend
    banner.

Every rebuild gives the copied companies brand-new auto-increment ids
(the companies table is wiped and reinserted fresh), which would
silently blank out two things keyed to the OLD ids if nothing else ran
afterward: companies.display_name and the company_research table (both
built by separate, narrower sync scripts - see
scripts/sync_company_display_names_to_demo.py and
scripts/sync_company_research_to_demo.py). This script therefore calls
both of those, in-process, as a required last step of every real
rebuild (both --force and the first-ever populate of an empty target) -
not optional, and not something a caller needs to remember to run
separately. Both are themselves idempotent against the same source/
target pair, so this is safe to run every time.

The target must already have the schema migrated (alembic upgrade head
against TARGET_DATABASE_URL) before running this - same convention as
every other path in this project, see CLAUDE.md. This script is meant
for the local Docker dev mirror database (docker-compose's `db`
service, host port 5433), never for production - TARGET_DATABASE_URL
must point there or at some other non-production database, and the
same-database check above is the only thing this script can verify
automatically.

Idempotent by default: if the target's job_postings table is already
non-empty, this logs one line and exits without touching anything,
mirroring scripts/seed_sample_job_postings.py's own gate. Pass --force
to wipe the demo-relevant tables on the target first and rebuild from
scratch (used for iterating on this script locally - never run --force
against anything other than a throwaway dev database).
"""
import argparse
import logging
import os
import sys
import time
from collections import defaultdict
from datetime import date

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, SRC_DIR)
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS_DIR)

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from sqlalchemy import create_engine, func, select, text  # noqa: E402
from sqlalchemy.dialects.postgresql import insert as pg_insert  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from huntloop.api.sponsor_summary import _get_sponsorship_summary_from_lca  # noqa: E402
from huntloop.db_models import (  # noqa: E402
    Company,
    DemoMeta,
    JobLocation,
    JobMetadata,
    JobPosting,
    JobSource,
    LcaDisclosure,
    ResumeVersion,
    SponsorFiscalYearAggregate,
    SponsorOverallAggregate,
)
from huntloop.logging_config import setup_logging  # noqa: E402
from huntloop.text_cleaning import clean_text  # noqa: E402

import sync_company_display_names_to_demo  # noqa: E402
import sync_company_research_to_demo  # noqa: E402

setup_logging()
logger = logging.getLogger(__name__)

PER_COMPANY_CAP = 40
# A single INSERT statement covering every selected posting (now ~19,000
# rather than the old ~10,000) proved too large for Neon's direct
# connection in practice - it dropped the SSL connection mid-statement.
# Chunking keeps each round trip small regardless of total dataset size.
COPY_CHUNK_SIZE = 1000
ATS_PLATFORMS = (
    "greenhouse_api",
    "lever_api",
    "workday_api",
    "smartrecruiters_api",
    "ashby_api",
    "icims_portal",
    "gem_api",
)

FICTIONAL_RESUME_TEXT = """Jordan Alvarez
Backend Software Engineer
jordan.alvarez.demo@example.com | (555) 010-7788 | Austin, TX

Summary
Backend engineer with 6 years of experience building and operating REST
and GraphQL APIs, event driven services, and data pipelines on AWS.
Comfortable owning a service from design through on call, with a focus
on Python, Postgres, and distributed systems fundamentals.

Experience

Senior Software Engineer, Backend - Northwind Freight Systems
2022 to present
Designed and built a shipment tracking API serving over 2 million
requests a day, using Python, FastAPI, and PostgreSQL. Introduced
read replicas and query caching that cut p95 latency by 40 percent.
Led the migration of a monolithic order service into three
independently deployable services communicating over Kafka. Mentored
two junior engineers and ran the team's on call rotation.

Software Engineer - Lakeside Analytics
2019 to 2022
Built ETL pipelines in Python and Airflow that moved several terabytes
a day from operational Postgres databases into a Snowflake warehouse.
Wrote the internal library used across the engineering org for
schema migrations and database connection pooling. Added integration
tests and CI checks that reduced production incidents tied to schema
drift.

Software Engineer, New Grad - Lakeside Analytics
2018 to 2019
Worked on the public facing API gateway, implementing rate limiting,
authentication middleware, and structured request logging in Python
and Flask. Shipped a Docker based local development setup that cut new
engineer onboarding time from two days to two hours.

Skills
Python, SQL, PostgreSQL, FastAPI, Flask, Django, Docker, Kubernetes,
AWS, Kafka, Redis, REST APIs, GraphQL, CI CD, Airflow, Git, pytest,
system design, distributed systems, database design, query
optimization

Education
B.S. in Computer Science, University of Texas at Austin, 2018

Projects
Open source contributor to a Python database migration tool, adding
support for zero downtime column renames used by several hundred
downstream projects.
"""


def _chunks(values: list[dict], size: int) -> list[list[dict]]:
    return [values[i : i + size] for i in range(0, len(values), size)]


def _refuse_if_same_database(source_url: str, target_url: str) -> None:
    source = make_url(source_url)
    target = make_url(target_url)
    source_identity = (source.host, source.port, source.database)
    target_identity = (target.host, target.port, target.database)
    if source_identity == target_identity:
        raise SystemExit(
            f"SOURCE_DATABASE_URL and TARGET_DATABASE_URL both resolve to {source_identity} - "
            f"refusing to run. This script must never write to the database it reads from."
        )


def _make_source_engine(source_url: str):
    # default_transaction_read_only=on makes every statement on every
    # connection from this engine reject a write at the Postgres level,
    # not just at the level of this script choosing not to issue one.
    return create_engine(source_url, connect_args={"options": "-c default_transaction_read_only=on"})


def _select_sample_postings(source_session) -> list[dict]:
    """Returns one dict per selected posting (id, company_id, platform).
    Includes every company on every platform that has at least one
    eligible posting (is_relevant IS TRUE AND embedding IS NOT NULL) -
    company coverage is the goal, not a fixed total-postings count - with
    each company's own postings capped at PER_COMPANY_CAP, preferring
    postings with a US location, so one huge board doesn't balloon the
    copy at the expense of row count elsewhere."""
    selected: list[dict] = []
    for platform in ATS_PLATFORMS:
        has_us_location = (
            select(JobLocation.id)
            .where(JobLocation.job_id == JobPosting.id, JobLocation.location_country == "United States")
            .exists()
        )
        rows = source_session.execute(
            select(JobPosting.id, JobPosting.company_id, has_us_location.label("has_us_location"))
            .join(JobSource, JobSource.id == JobPosting.source_id)
            .where(
                JobSource.name == platform,
                JobPosting.is_relevant.is_(True),
                JobPosting.embedding.isnot(None),
            )
            .order_by(has_us_location.desc(), JobPosting.id.asc())
        ).all()

        company_counts: dict[int, int] = defaultdict(int)
        companies_seen: set[int] = set()
        taken = 0
        for row in rows:
            companies_seen.add(row.company_id)
            if company_counts[row.company_id] >= PER_COMPANY_CAP:
                continue
            company_counts[row.company_id] += 1
            selected.append({"id": row.id, "company_id": row.company_id, "platform": platform})
            taken += 1
        logger.info(
            f"build_demo_dataset: {platform} - selected {taken} postings across "
            f"{len(companies_seen)} companies (all eligible companies on this platform)."
        )

    return selected


def _copy_job_sources(source_session, target_session) -> dict[str, int]:
    rows = source_session.execute(select(JobSource.name).where(JobSource.name.in_(ATS_PLATFORMS))).all()
    values = [{"name": r[0]} for r in rows]
    stmt = pg_insert(JobSource).values(values).on_conflict_do_nothing(index_elements=["name"])
    target_session.execute(stmt)
    target_session.commit()
    return dict(
        target_session.execute(select(JobSource.name, JobSource.id).where(JobSource.name.in_(ATS_PLATFORMS))).all()
    )


def _copy_companies(source_session, target_session, company_ids: set[int]) -> dict[int, int]:
    rows = source_session.execute(select(Company).where(Company.id.in_(company_ids))).scalars().all()
    values = [
        {
            "name": c.name,
            "website": c.website,
            "h1b_sponsorship": c.h1b_sponsorship,
            "ats_platform": c.ats_platform,
            "ats_token": c.ats_token,
            "careers_url": c.careers_url,
            "matched_sponsor_employer_name": c.matched_sponsor_employer_name,
            "sponsor_checked_at": c.sponsor_checked_at,
        }
        for c in rows
    ]
    stmt = pg_insert(Company).values(values).on_conflict_do_nothing(index_elements=["name"])
    target_session.execute(stmt)
    target_session.commit()

    names = [v["name"] for v in values]
    target_id_by_name = dict(
        target_session.execute(select(Company.name, Company.id).where(Company.name.in_(names))).all()
    )
    old_id_to_new_id = {c.id: target_id_by_name[c.name] for c in rows if c.name in target_id_by_name}
    logger.info(f"build_demo_dataset: companies - {len(values)} copied.")
    return old_id_to_new_id


def _copy_postings(
    source_session, target_session, selected: list[dict], company_id_map: dict[int, int], source_id_map: dict[str, int]
) -> dict[int, int]:
    posting_ids = [s["id"] for s in selected]
    rows = source_session.execute(select(JobPosting).where(JobPosting.id.in_(posting_ids))).scalars().all()
    platform_by_posting_id = {s["id"]: s["platform"] for s in selected}

    values = []
    for p in rows:
        new_company_id = company_id_map.get(p.company_id)
        new_source_id = source_id_map.get(platform_by_posting_id[p.id])
        if new_company_id is None or new_source_id is None:
            continue
        values.append(
            {
                "gh_job_id": p.gh_job_id,
                "job_title": p.job_title,
                "job_url": p.job_url,
                "department": p.department,
                "department_category": p.department_category,
                "employment_type": p.employment_type,
                "job_description": p.job_description,
                "date_posted": p.date_posted,
                "is_active": p.is_active,
                "embedding": p.embedding,
                "matched_skills": p.matched_skills,
                "missing_skills": p.missing_skills,
                "is_relevant": p.is_relevant,
                "company_id": new_company_id,
                "source_id": new_source_id,
            }
        )

    for chunk in _chunks(values, COPY_CHUNK_SIZE):
        stmt = pg_insert(JobPosting).values(chunk).on_conflict_do_nothing(index_elements=["job_url"])
        target_session.execute(stmt)
        target_session.commit()

    urls = [v["job_url"] for v in values]
    new_id_by_url = dict(
        target_session.execute(select(JobPosting.job_url, JobPosting.id).where(JobPosting.job_url.in_(urls))).all()
    )
    old_id_to_new_id = {p.id: new_id_by_url[p.job_url] for p in rows if p.job_url in new_id_by_url}
    logger.info(f"build_demo_dataset: job_postings - {len(values)} copied.")
    return old_id_to_new_id


def _copy_job_locations(source_session, target_session, job_id_map: dict[int, int]) -> None:
    old_ids = list(job_id_map.keys())
    rows = source_session.execute(select(JobLocation).where(JobLocation.job_id.in_(old_ids))).scalars().all()
    values = [
        {
            "job_id": job_id_map[r.job_id],
            "location_name": r.location_name,
            "location_city": r.location_city,
            "location_region": r.location_region,
            "location_country": r.location_country,
            "location_is_remote": r.location_is_remote,
            "location_canonical": r.location_canonical,
        }
        for r in rows
        if r.job_id in job_id_map
    ]
    for chunk in _chunks(values, COPY_CHUNK_SIZE):
        target_session.bulk_insert_mappings(JobLocation, chunk)
        target_session.commit()
    logger.info(f"build_demo_dataset: job_locations - {len(values)} copied.")


def _copy_job_metadata(source_session, target_session, job_id_map: dict[int, int]) -> None:
    old_ids = list(job_id_map.keys())
    rows = source_session.execute(select(JobMetadata).where(JobMetadata.job_id.in_(old_ids))).scalars().all()
    values = [
        {"job_id": job_id_map[r.job_id], "metadata_json": r.metadata_json}
        for r in rows
        if r.job_id in job_id_map
    ]
    for chunk in _chunks(values, COPY_CHUNK_SIZE):
        target_session.bulk_insert_mappings(JobMetadata, chunk)
        target_session.commit()
    logger.info(f"build_demo_dataset: job_metadata - {len(values)} copied.")


def _build_sponsor_aggregates(source_session, target_session, employer_names: set[str]) -> None:
    if not employer_names:
        logger.info("build_demo_dataset: sponsor aggregates - no sampled company has a resolved sponsor match.")
        return

    names = sorted(employer_names)

    per_year_rows = source_session.execute(
        select(
            LcaDisclosure.employer_name_normalized,
            LcaDisclosure.fiscal_year,
            func.count().label("total_filings"),
            func.count(func.distinct(LcaDisclosure.job_title)).label("distinct_titles"),
            func.percentile_cont(0.5)
            .within_group(LcaDisclosure.wage_rate_of_pay_from)
            .filter(LcaDisclosure.wage_unit_of_pay == "Year")
            .label("median_wage"),
        )
        .where(LcaDisclosure.employer_name_normalized.in_(names))
        .group_by(LcaDisclosure.employer_name_normalized, LcaDisclosure.fiscal_year)
    ).all()

    fiscal_year_values = [
        {
            "employer_name_normalized": r.employer_name_normalized,
            "fiscal_year": r.fiscal_year,
            "total_filings": r.total_filings,
            "distinct_titles": r.distinct_titles,
            "median_wage": r.median_wage,
        }
        for r in per_year_rows
    ]
    if fiscal_year_values:
        target_session.execute(pg_insert(SponsorFiscalYearAggregate).values(fiscal_year_values))
        target_session.commit()

    overall_values = []
    for name in names:
        # Reuses the real production aggregation function unchanged, read
        # only against the source database, so a demo deployment's
        # sponsor numbers for a sampled company match what production
        # itself would compute for that same company.
        summary = _get_sponsorship_summary_from_lca(source_session, name)
        if summary is None:
            continue
        overall_values.append(
            {
                "employer_name_normalized": name,
                "most_recent_fiscal_year": summary.most_recent_fiscal_year,
                "total_lcas_most_recent_fiscal_year": summary.total_lcas_most_recent_fiscal_year,
                "median_wage": summary.median_wage,
                "most_frequent_job_title": summary.most_frequent_job_title,
                "latest_case_status": summary.latest_case_status,
            }
        )
    if overall_values:
        target_session.execute(pg_insert(SponsorOverallAggregate).values(overall_values))
        target_session.commit()

    logger.info(
        f"build_demo_dataset: sponsor aggregates - {len(fiscal_year_values)} fiscal year rows, "
        f"{len(overall_values)} overall rows, for {len(names)} employer(s)."
    )


def _write_fictional_resume(target_session) -> None:
    from huntloop.embeddings import embed_text  # lazy - needs torch, see module docstring

    embedding = embed_text(FICTIONAL_RESUME_TEXT)
    resume = ResumeVersion(
        version_number=1,
        file_path="demo/jordan_alvarez_fictional_resume.txt",
        extracted_text=FICTIONAL_RESUME_TEXT,
        is_active=True,
        embedding=embedding,
    )
    target_session.add(resume)
    target_session.commit()
    logger.info(f"build_demo_dataset: fictional resume written ({len(clean_text(FICTIONAL_RESUME_TEXT))} cleaned chars).")


def _write_demo_meta(target_session, snapshot_date: date) -> None:
    target_session.add(DemoMeta(snapshot_date=snapshot_date))
    target_session.commit()
    logger.info(f"build_demo_dataset: demo_meta snapshot_date={snapshot_date}.")


def _force_clear_target(target_session) -> None:
    for table in (
        "job_locations",
        "job_metadata",
        "job_postings",
        "sponsor_fiscal_year_aggregates",
        "sponsor_overall_aggregates",
        "resume_versions",
        "job_applications",
        "demo_meta",
        "company_research",
        "companies",
        "job_sources",
    ):
        target_session.execute(text(f"DELETE FROM {table}"))
    target_session.commit()
    logger.info("build_demo_dataset: --force - cleared existing target tables.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="Clear existing target tables first.")
    args = parser.parse_args()

    source_url = os.environ.get("SOURCE_DATABASE_URL")
    target_url = os.environ.get("TARGET_DATABASE_URL")
    if not source_url or not target_url:
        raise SystemExit("SOURCE_DATABASE_URL and TARGET_DATABASE_URL must both be set.")

    _refuse_if_same_database(source_url, target_url)

    t0 = time.perf_counter()

    source_engine = _make_source_engine(source_url)
    target_engine = create_engine(target_url)
    SourceSession = sessionmaker(bind=source_engine)
    TargetSession = sessionmaker(bind=target_engine)
    source_session = SourceSession()
    target_session = TargetSession()

    try:
        existing = target_session.execute(select(func.count()).select_from(JobPosting)).scalar_one()
        if existing > 0 and not args.force:
            logger.info(
                f"Target job_postings already has {existing} row(s) - skipping "
                f"(pass --force to rebuild from scratch)."
            )
            return
        if args.force:
            _force_clear_target(target_session)

        selected = _select_sample_postings(source_session)
        logger.info(f"build_demo_dataset: selected {len(selected)} postings total.")

        source_id_map = _copy_job_sources(source_session, target_session)
        company_ids = {s["company_id"] for s in selected}
        company_id_map = _copy_companies(source_session, target_session, company_ids)

        job_id_map = _copy_postings(source_session, target_session, selected, company_id_map, source_id_map)
        _copy_job_locations(source_session, target_session, job_id_map)
        _copy_job_metadata(source_session, target_session, job_id_map)

        sampled_companies = source_session.execute(
            select(Company.matched_sponsor_employer_name).where(
                Company.id.in_(company_ids), Company.matched_sponsor_employer_name.isnot(None)
            )
        ).scalars().all()
        _build_sponsor_aggregates(source_session, target_session, set(sampled_companies))

        _write_fictional_resume(target_session)
        _write_demo_meta(target_session, date.today())
    finally:
        source_session.close()
        target_session.close()

    # Required, not optional: every full rebuild gives the copied companies
    # brand-new auto-increment ids, so display_name and company_research -
    # both keyed off the old ids and therefore silently blown away by the
    # rebuild above - must be repopulated every time, not just the first
    # time. Each of these opens its own fresh connections and is itself
    # idempotent (a second run is a safe no-op), so running them here
    # unconditionally after a real rebuild is safe.
    logger.info("build_demo_dataset: running required post-build syncs (company display names, company research)...")
    sync_company_display_names_to_demo.main(source_url=source_url, neon_url=target_url)
    sync_company_research_to_demo.main(source_url=source_url, neon_url=target_url)

    elapsed = time.perf_counter() - t0
    logger.info(f"build_demo_dataset: complete. Elapsed: {elapsed:.1f}s")


if __name__ == "__main__":
    main()
