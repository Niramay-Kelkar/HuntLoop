"""
One-off script: backfill job_postings.employment_type for existing iCIMS
(`icims_portal`) rows by re-fetching each row's own stored job_url (the
canonical per-job detail page) and re-parsing its JSON-LD `employmentType`
- the one source where this genuinely can't be done any cheaper.

Context: see scripts/backfill_employment_type_from_metadata.py's
docstring for the general background, and huntloop.icims_portal /
IcimsScraper's own docstrings for why iCIMS has no list-level or stored-
metadata shortcut: `employmentType` only exists in each job's own
schema.org JSON-LD block on its detail page, never on the listing page
and never in this project's stored metadata_json (which only holds
portalJobId/fieldSource/hiringOrganization/validThrough/identifier - see
IcimsScraper._to_item). This is the same shape the department fix hit
for iCIMS (see CLAUDE.md) and needed a real re-scrape for.

This script re-fetches by DETAIL URL directly (job_postings.job_url is
already the canonical detail page for every existing row) rather than
re-walking each tenant's listing pages - there is no new posting to
discover here, only an existing row's employment_type to fill in, so
listing pagination would be pure overhead.

Same politeness posture as IcimsScraper: honest identifying User-Agent,
one request in flight per host, a real delay between requests to the
same host, and robots.txt re-checked per tenant before touching it (a
disallow is never bypassed, matching the spider's own hard rule) - even
though every tenant here was already onboarded past that gate once, this
re-confirms nothing changed since.

Different tenants (different hosts) are processed concurrently via a
thread pool for real throughput - courteous to any one iCIMS tenant
(still >= REQUEST_DELAY seconds between two requests to the SAME host)
while not serializing the whole, much larger cross-tenant backlog behind
the single slowest tenant. Only `employment_type IS NULL` rows with a
real new value (normalized via huntloop.employment_type) are touched -
no other column, and no row is inserted or deleted. Safe to interrupt
and re-run: already-filled rows are skipped by the same IS NULL filter,
and this only ever reads a per-job GET (no writes to the source site).
"""
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlsplit

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, SRC_DIR)

import requests
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from huntloop.db_models import Company, JobPosting
from huntloop.employment_type import normalize_employment_type
from huntloop.icims_portal import extract_jobposting_jsonld, jobposting_fields, robots_allows_listing, with_in_iframe
from huntloop.settings import DATABASE_URL

logger = logging.getLogger(__name__)

USER_AGENT = "HuntLoop/1.0 (sponsorship-aware job aggregator; sponsorship-matching research)"
REQUEST_DELAY = 1.5  # seconds between two requests to the SAME tenant host
REQUEST_TIMEOUT = 20
MAX_TENANT_WORKERS = 8


def _tenant_allows(slug: str) -> bool:
    try:
        resp = requests.get(f"https://careers-{slug}.icims.com/robots.txt", timeout=REQUEST_TIMEOUT)
    except requests.RequestException:
        return True  # unreachable robots.txt is treated as permissive, matching the spider
    if resp.status_code >= 400:
        return True
    return robots_allows_listing(resp.text, USER_AGENT)


def _backfill_one_tenant(engine, slug: str, job_urls: list[tuple[int, str]]) -> tuple[int, int]:
    """Returns (seen, filled) for one tenant. Opens its own Session -
    each tenant runs in its own thread, and SQLAlchemy Sessions aren't
    thread-safe to share."""
    Session = sessionmaker(bind=engine)
    session = Session()
    seen = 0
    filled = 0
    try:
        if not _tenant_allows(slug):
            logger.warning(f"{slug}: robots.txt disallows /jobs/search - skipping tenant entirely")
            return 0, 0

        for job_id, job_url in job_urls:
            seen += 1
            try:
                resp = requests.get(
                    with_in_iframe(job_url),
                    headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
                    timeout=REQUEST_TIMEOUT,
                )
            except requests.RequestException as exc:
                logger.warning(f"{slug}: request failed for job {job_id} ({exc}) - skipping job")
                time.sleep(REQUEST_DELAY)
                continue
            time.sleep(REQUEST_DELAY)

            if resp.status_code != 200:
                logger.warning(f"{slug}: HTTP {resp.status_code} for job {job_id} - skipping job")
                continue

            node = extract_jobposting_jsonld(resp.text)
            if node is None:
                continue
            fields = jobposting_fields(node)
            normalized = normalize_employment_type(fields.get("employment_type"))
            if not normalized:
                continue

            job_post = session.get(JobPosting, job_id)
            if job_post is not None and job_post.employment_type is None:
                job_post.employment_type = normalized
                session.commit()
                filled += 1
    finally:
        session.close()
    return seen, filled


def main():
    engine = create_engine(DATABASE_URL, echo=False)
    Session = sessionmaker(bind=engine)
    session = Session()

    start = time.perf_counter()
    try:
        companies = session.query(Company).filter(Company.ats_platform == "icims").all()
        by_tenant: dict[str, list[tuple[int, str]]] = {}
        rows = (
            session.query(JobPosting.id, JobPosting.job_url, Company.ats_token)
            .join(Company, Company.id == JobPosting.company_id)
            .filter(Company.ats_platform == "icims", JobPosting.employment_type.is_(None))
            .all()
        )
        for job_id, job_url, ats_token in rows:
            slug = ats_token or urlsplit(job_url).hostname.split(".")[0].removeprefix("careers-")
            by_tenant.setdefault(slug, []).append((job_id, job_url))
    finally:
        session.close()

    logger.info(
        f"iCIMS employment_type backfill starting - {len(by_tenant)} tenants, "
        f"{sum(len(v) for v in by_tenant.values())} rows with NULL employment_type"
    )

    total_seen = 0
    total_filled = 0
    with ThreadPoolExecutor(max_workers=min(MAX_TENANT_WORKERS, max(len(by_tenant), 1))) as pool:
        futures = {
            pool.submit(_backfill_one_tenant, engine, slug, job_urls): slug
            for slug, job_urls in by_tenant.items()
        }
        for future in as_completed(futures):
            slug = futures[future]
            try:
                seen, filled = future.result()
            except Exception:
                logger.exception(f"{slug}: tenant backfill raised, skipping the rest of its rows")
                continue
            total_seen += seen
            total_filled += filled
            logger.info(f"{slug}: done - {seen} rows fetched, {filled} filled")

    elapsed = time.perf_counter() - start
    logger.info(
        f"iCIMS employment_type backfill complete - "
        f"{total_seen} rows fetched, {total_filled} filled, in {elapsed:.1f}s"
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
