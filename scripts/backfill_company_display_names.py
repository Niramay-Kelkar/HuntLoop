"""
One-off backfill: populate companies.display_name for every existing
company across all 7 ATS platforms this project scrapes.

Does NOT touch companies.name (the lowercase ATS slug) or any
comparison/join/filter that uses it - purely fills the separate,
additive display_name column.

Phase A (Greenhouse, SmartRecruiters, Gem):
  - Greenhouse: one GET to boards-api.greenhouse.io/v1/boards/{slug} per
    company (huntloop.company_display_name.fetch_greenhouse_display_name).
  - SmartRecruiters: one GET to the postings list endpoint (limit=1) per
    company - same `company.identifier`/`company.name` fields the spider
    now reads live, run through the same heuristic
    (pick_smartrecruiters_display_name).
  - Gem: reuses scripts/discover_gem_job_board.py's check_slug(slug)
    unchanged (it already extracts teamDisplayName) - one GraphQL call
    per company.

Phase B (Lever, Ashby, iCIMS, Workday):
  - Lever: one GET to jobs.lever.co/{slug}, <title> tag taken as-is
    (huntloop.company_display_name.fetch_lever_display_name).
  - Ashby: one GET to jobs.ashbyhq.com/{slug}, <title> minus a trailing
    " Jobs" suffix (fetch_ashby_display_name).
  - iCIMS: one GET to the tenant's portal page, <title> minus a leading
    "Job Listings at " prefix (fetch_icims_display_name).
  - Workday: NO network call - there is no ATS-side proper-name source
    for Workday at all (confirmed during Phase A/B investigation: no
    JSON field, empty rendered page title, name only buried in
    unstructured prose). Falls back to the existing
    companies.matched_sponsor_employer_name (DOL LCA fuzzy-match),
    case-folded via huntloop.company_display_name.casefold_legal_entity_name
    - left NULL where no sponsor match exists, exactly like today.

Idempotent: skips any company that already has a non-null display_name
unless --force is passed. Safe to re-run.

Run:
    PYTHONPATH=src python scripts/backfill_company_display_names.py
    PYTHONPATH=src python scripts/backfill_company_display_names.py --force
    PYTHONPATH=src python scripts/backfill_company_display_names.py --platform greenhouse
"""
import argparse
import json
import logging
import sys
import time

import requests
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from huntloop.company_display_name import (
    casefold_legal_entity_name,
    fetch_ashby_display_name,
    fetch_greenhouse_display_name,
    fetch_icims_display_name,
    fetch_lever_display_name,
    pick_smartrecruiters_display_name,
)
from huntloop.db_models import Company
from huntloop.settings import DATABASE_URL

sys.path.insert(0, "scripts")
import discover_gem_job_board as gem_resolver  # noqa: E402  (reused unchanged)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_SR_API = "https://api.smartrecruiters.com/v1"
REQUEST_DELAY = 0.3


def _smartrecruiters_display_name(company: Company) -> tuple[str | None, str]:
    """Returns (display_name_or_None, reason_if_none)."""
    slug = company.ats_token or company.name
    try:
        resp = requests.get(f"{_SR_API}/companies/{slug}/postings?limit=1", timeout=15)
    except requests.RequestException as exc:
        return None, f"request failed ({exc})"
    if resp.status_code != 200:
        return None, f"HTTP {resp.status_code}"
    try:
        data = resp.json()
    except ValueError:
        return None, "non-JSON response"
    content = data.get("content") or []
    if not content:
        return None, f"no postings (totalFound={data.get('totalFound')!r})"
    posted_company = content[0].get("company") or {}
    picked = pick_smartrecruiters_display_name(
        slug, posted_company.get("identifier"), posted_company.get("name")
    )
    if picked is None:
        return None, (
            f"neither field qualified (identifier={posted_company.get('identifier')!r}, "
            f"name={posted_company.get('name')!r})"
        )
    return picked, ""


def _greenhouse_display_name(company: Company) -> tuple[str | None, str]:
    slug = company.ats_token or company.name
    name = fetch_greenhouse_display_name(slug)
    return name, ("" if name else "fetch failed or no name field")


def _gem_display_name(company: Company) -> tuple[str | None, str]:
    slug = company.ats_token or company.name
    hit = gem_resolver.check_slug(slug)
    if hit is None:
        return None, "request failed"
    if not hit.get("is_board"):
        return None, "slug is not a real jobs.gem.com board"
    org = hit.get("org_name")
    return org, ("" if org else "board resolved but gave no org name")


def _lever_display_name(company: Company) -> tuple[str | None, str]:
    slug = company.ats_token or company.name
    name = fetch_lever_display_name(slug)
    return name, ("" if name else "fetch failed, empty board, or no title tag")


def _ashby_display_name(company: Company) -> tuple[str | None, str]:
    slug = company.ats_token or company.name
    name = fetch_ashby_display_name(slug)
    return name, ("" if name else "fetch failed or title didn't match the expected pattern")


def _icims_display_name(company: Company) -> tuple[str | None, str]:
    slug = company.ats_token or company.name
    name = fetch_icims_display_name(slug)
    return name, ("" if name else "fetch failed or title didn't match the expected pattern")


def _workday_display_name(company: Company) -> tuple[str | None, str]:
    """No network call - see this module's docstring. Falls back to the
    already-stored DOL sponsor match, case-folded."""
    raw = company.matched_sponsor_employer_name
    if not raw:
        return None, "no resolved DOL sponsor match (matched_sponsor_employer_name is NULL)"
    return casefold_legal_entity_name(raw), ""


_FETCHERS = {
    "greenhouse": _greenhouse_display_name,
    "smartrecruiters": _smartrecruiters_display_name,
    "gem": _gem_display_name,
    "lever": _lever_display_name,
    "ashby": _ashby_display_name,
    "icims": _icims_display_name,
    "workday": _workday_display_name,
}


def run(session, platform: str, force: bool) -> tuple[int, int, list[tuple[str, str]]]:
    """Returns (filled, skipped_already_set, [(slug, reason), ...] for null results)."""
    fetch = _FETCHERS[platform]
    query = session.query(Company).filter_by(ats_platform=platform)
    companies = query.order_by(Company.id).all()

    filled = skipped = 0
    stayed_null: list[tuple[str, str]] = []

    for company in companies:
        if company.display_name and not force:
            skipped += 1
            continue
        slug = company.ats_token or company.name
        display_name, reason = fetch(company)
        if display_name:
            company.display_name = display_name
            filled += 1
            logger.info(f"{platform}: {slug!r} -> {display_name!r}")
        else:
            stayed_null.append((slug, reason))
            logger.info(f"{platform}: {slug!r} -> NULL ({reason})")
        session.commit()
        if platform != "workday":  # no network call for workday, no need to pace it
            time.sleep(REQUEST_DELAY)

    return filled, skipped, stayed_null


_ALL_PLATFORMS = ["greenhouse", "smartrecruiters", "gem", "lever", "ashby", "icims", "workday"]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true", help="re-fetch even for companies that already have a display_name")
    ap.add_argument("--platform", choices=_ALL_PLATFORMS,
                     help="only backfill this one platform (default: all seven)")
    args = ap.parse_args()

    engine = create_engine(DATABASE_URL, echo=False)
    session = sessionmaker(bind=engine)()

    platforms = [args.platform] if args.platform else _ALL_PLATFORMS

    try:
        for platform in platforms:
            logger.info(f"=== {platform} ===")
            filled, skipped, stayed_null = run(session, platform, args.force)
            total = filled + skipped + len(stayed_null)
            logger.info(
                f"{platform}: {filled}/{total} filled, {skipped}/{total} already had a value, "
                f"{len(stayed_null)}/{total} stayed NULL"
            )
            if stayed_null:
                logger.info(f"{platform}: companies that stayed NULL: " + json.dumps(stayed_null))
    finally:
        session.close()


if __name__ == "__main__":
    main()
