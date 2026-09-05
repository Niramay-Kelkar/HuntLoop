"""
One-off script: backfill job_postings.employment_type for existing
SmartRecruiters (`smartrecruiters_api`) rows using ONLY the SmartRecruiters
list endpoint - no per-job detail requests.

Context: see scripts/backfill_employment_type_from_metadata.py's
docstring for the general background. SmartRecruiters' stored
metadata_json does not carry `typeOfEmployment` (confirmed directly), but
its list response already does, same as `department` did for the
existing department backfill (scripts/backfill_department_smartrecruiters.py,
which this script mirrors exactly - same reasoning against a full
per-job-detail re-scrape being far too slow for this narrow purpose):

    GET {API}/companies/{companyId}/postings?limit=100&offset=N
    -> {"totalFound": M, "content": [{id, ..., typeOfEmployment: {id, label}, ...}]}

Existing rows are matched by the exact same gh_job_id construction
SmartRecruitersScraper uses. Only `employment_type IS NULL` rows with a
real new value (normalized via huntloop.employment_type) are touched -
no other column, and no row is inserted.
"""
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
from huntloop.employment_type import normalize_employment_type
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
                raw = (posting.get("typeOfEmployment") or {}).get("label")
                normalized = normalize_employment_type(raw)
                if not normalized:
                    continue
                gh_job_id = _gh_job_id(company_id, posting["id"])
                job_post = session.query(JobPosting).filter_by(gh_job_id=gh_job_id).first()
                if job_post is not None and job_post.employment_type is None:
                    job_post.employment_type = normalized
                    company_filled += 1
                    total_filled += 1
            if company_filled:
                session.commit()
                logger.info(f"{company_id}: filled {company_filled} employment_type values")
    finally:
        session.close()

    elapsed = time.perf_counter() - start
    logger.info(
        f"SmartRecruiters list-only employment_type backfill complete - "
        f"{total_companies} companies, {total_postings_seen} postings seen, "
        f"{total_filled} rows filled, in {elapsed:.1f}s"
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
