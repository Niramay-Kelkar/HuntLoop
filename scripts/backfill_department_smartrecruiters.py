"""
One-off script: backfill job_postings.department for existing
SmartRecruiters (`smartrecruiters_api`) rows using ONLY the SmartRecruiters
list endpoint - no per-job detail requests.

Context: see scripts/backfill_department_lever.py's docstring for the
general department-NULL background. SmartRecruiters is a special case
worth its own script rather than reusing SmartRecruitersScraper's
existing spider/pipeline path (scripts/scrape_smartrecruiters.py): its
list response

    GET {API}/companies/{companyId}/postings?limit=100&offset=N
    -> {"totalFound": M, "content": [{id, name, ..., department, ...}]}

already carries the `department` object per posting (see
SmartRecruitersScraper's own module docstring) - the per-job DETAIL
request that spider makes exists only to fetch the job description
(needed for a brand-new posting's relevance/embedding), not department.
A full re-scrape via the real spider was tried first and found to be far
too slow for this narrow purpose (~19k detail requests, one per posting,
at ~2hrs for ~30 of 224 companies) - this script does the same
department backfill with one list call per ~100 postings instead.

Existing rows are matched by the exact same gh_job_id construction
SmartRecruitersScraper uses (`{company_id}_{posting_id}`, falling back to
`sr_{posting_id}` if that's over 50 chars) - so a row this script updates
is provably the same row a real re-scrape would have matched via
JobDataPipeline's own dedup-and-backfill logic
(see JobDataPipeline.process_item's repost handling). Only
`department IS NULL` rows with a real new value are touched - no other
column, and no row is inserted (a list-only pass has no job description,
so a genuinely new posting is deliberately left uninserted here; it will
be picked up by a normal scheduled scrape/pipeline run instead).
"""
import json
import logging
import os
import sys
import time

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, SRC_DIR)

import requests
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from huntloop.db_models import Company, JobPosting
from huntloop.settings import DATABASE_URL

logger = logging.getLogger(__name__)

_API = "https://api.smartrecruiters.com/v1"
PAGE_SIZE = 100
MAX_PAGES = 200
REQUEST_DELAY = 0.3


def _gh_job_id(company_id: str, posting_id: str) -> str:
    namespaced = f"{company_id}_{posting_id}"
    return namespaced if len(namespaced) <= 50 else f"sr_{posting_id}"


def _iter_list_postings(company_id: str):
    """Yields every posting dict for a company via list-only pagination."""
    offset = 0
    total = None
    page = 0
    while True:
        resp = requests.get(
            f"{_API}/companies/{company_id}/postings",
            params={"limit": PAGE_SIZE, "offset": offset},
            timeout=30,
        )
        time.sleep(REQUEST_DELAY)
        if resp.status_code != 200:
            logger.warning(f"{company_id}: HTTP {resp.status_code} at offset {offset}, stopping")
            return
        try:
            data = resp.json()
        except ValueError:
            logger.warning(f"{company_id}: non-JSON response at offset {offset}, stopping")
            return

        content = data.get("content")
        if not isinstance(content, list):
            return
        if offset == 0:
            total = data.get("totalFound")
            if not isinstance(total, int) or total == 0:
                logger.info(f"{company_id}: totalFound={total!r} - empty/stale board, skipping")
                return

        for posting in content:
            if isinstance(posting, dict) and posting.get("id"):
                yield posting

        page += 1
        offset += PAGE_SIZE
        if not isinstance(total, int) or offset >= total or page >= MAX_PAGES:
            return


def main():
    engine = create_engine(DATABASE_URL, echo=False)
    Session = sessionmaker(bind=engine)
    session = Session()

    start = time.perf_counter()
    total_companies = 0
    total_postings_seen = 0
    total_filled = 0

    try:
        companies = session.query(Company).filter(Company.ats_platform == "smartrecruiters").all()
        for company in companies:
            company_id = company.ats_token or company.name
            total_companies += 1
            company_filled = 0
            for posting in _iter_list_postings(company_id):
                total_postings_seen += 1
                dept = (posting.get("department") or {}).get("label")
                if not dept:
                    continue
                gh_job_id = _gh_job_id(company_id, posting["id"])
                job_post = session.query(JobPosting).filter_by(gh_job_id=gh_job_id).first()
                if job_post is not None and job_post.department is None:
                    job_post.department = dept
                    company_filled += 1
                    total_filled += 1
            if company_filled:
                session.commit()
                logger.info(f"{company_id}: filled {company_filled} department values")
    finally:
        session.close()

    elapsed = time.perf_counter() - start
    logger.info(
        f"SmartRecruiters list-only department backfill complete - "
        f"{total_companies} companies, {total_postings_seen} postings seen, "
        f"{total_filled} rows filled, in {elapsed:.1f}s"
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
