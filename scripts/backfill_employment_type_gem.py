"""
One-off script: backfill job_postings.employment_type for existing Gem
(`gem_api`) rows using ONLY the Gem list (JobBoardList) query - no
per-job detail requests. See GemScraper's own docstring:
`employmentType` is already inline on each posting in the list response,
so - unlike a real scrape, which also needs the detail query for the
job description - this backfill needs exactly one HTTP request per
company, regardless of job count.

Context: see scripts/backfill_employment_type_from_metadata.py's
docstring for the general background. Gem's stored metadata_json does
not carry `employmentType` (confirmed directly), so this needs a live
re-fetch.

Existing rows are matched by the exact same gh_job_id construction
GemScraper uses (`{slug}_{id}`, falling back to `gem_{id}` if over 50
chars, using the posting's `id` field - not `extId`). Only
`employment_type IS NULL` rows with a real new value (normalized via
huntloop.employment_type) are touched - no other column, and no row is
inserted.
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

_ENDPOINT = "https://jobs.gem.com/api/public/graphql/batch"
REQUEST_DELAY = 0.3

_LIST_QUERY = """
query JobBoardList($boardId: String!) {
  oatsExternalJobPostings(boardId: $boardId) {
    jobPostings { id job { employmentType } }
  }
}
""".strip()


def _gh_job_id(slug: str, job_id: str) -> str:
    namespaced = f"{slug}_{job_id}"
    return namespaced if len(namespaced) <= 50 else f"gem_{job_id}"


def main():
    engine = create_engine(DATABASE_URL, echo=False)
    Session = sessionmaker(bind=engine)
    session = Session()

    start = time.perf_counter()
    total_companies = 0
    total_postings_seen = 0
    total_filled = 0

    try:
        companies = session.query(Company).filter(Company.ats_platform == "gem").all()
        for company in companies:
            slug = company.ats_token or company.name
            total_companies += 1
            body = [{"operationName": "JobBoardList", "variables": {"boardId": slug}, "query": _LIST_QUERY}]
            resp = requests.post(_ENDPOINT, json=body, timeout=30)
            time.sleep(REQUEST_DELAY)
            if resp.status_code != 200:
                logger.warning(f"{slug}: HTTP {resp.status_code}, skipping")
                continue
            try:
                data = resp.json()
            except ValueError:
                logger.warning(f"{slug}: non-JSON response, skipping")
                continue

            try:
                postings = data[0]["data"]["oatsExternalJobPostings"]["jobPostings"]
            except (KeyError, IndexError, TypeError):
                logger.warning(f"{slug}: unexpected response shape, skipping")
                continue
            if not isinstance(postings, list):
                continue

            company_filled = 0
            for job in postings:
                if not isinstance(job, dict) or not job.get("id"):
                    continue
                total_postings_seen += 1
                job_meta = job.get("job") or {}
                normalized = normalize_employment_type(job_meta.get("employmentType"))
                if not normalized:
                    continue
                gh_job_id = _gh_job_id(slug, job["id"])
                job_post = session.query(JobPosting).filter_by(gh_job_id=gh_job_id).first()
                if job_post is not None and job_post.employment_type is None:
                    job_post.employment_type = normalized
                    company_filled += 1
                    total_filled += 1
            if company_filled:
                session.commit()
                logger.info(f"{slug}: filled {company_filled} employment_type values")
    finally:
        session.close()

    elapsed = time.perf_counter() - start
    logger.info(
        f"Gem list-only employment_type backfill complete - "
        f"{total_companies} companies, {total_postings_seen} postings seen, "
        f"{total_filled} rows filled, in {elapsed:.1f}s"
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
