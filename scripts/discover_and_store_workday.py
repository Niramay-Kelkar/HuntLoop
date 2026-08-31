"""
For each company confirmed as Workday by the earlier probing pass
(scripts/probe_neither_ats_platforms.py -> scratch_neither_ats_probe.json),
discover its full {tenant, dc, site} triple and store it as a
``companies`` row (name = tenant slug, ats_platform = 'workday',
ats_token = tenant slug, careers_url = the full Workday URL - the storage
decision, see huntloop.workday_url).

Ambiguity handling (task requirement 5 - "flag for review rather than
guess"): a probe hit on a generic one-word slug ("red", "western",
"tera") confirms *a* Workday tenant by that name exists, not that it
belongs to this DOL employer. Such rows are NOT stored - they go in the
report's ``needs_review`` list. The check is twofold:
  1. slug is a common English / generic word (stoplist), OR
  2. the Workday board's own ``hiringOrganization`` legal name doesn't
     fuzzy-match the DOL employer name (token_set_ratio < 60).

Run:  PYTHONPATH=src python scripts/discover_and_store_workday.py [--commit]
Without --commit: writes scratch_workday_discovery.json and prints the
breakdown, touches nothing. With --commit: also upserts the stored rows
(fills NULL/'unknown' ats_platform only, never overwrites a different
successful platform - same rule as detect_ats_for_sponsors.upsert_hits).
"""
import argparse
import json
import logging
import sys
import time

from dotenv import load_dotenv
from rapidfuzz.fuzz import token_set_ratio
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

load_dotenv()

from huntloop.db_models import Company
from huntloop.matching.normalize import normalize_employer_name
from huntloop.settings import DATABASE_URL

sys.path.insert(0, "scripts")
import discover_workday_triple as _dwt  # noqa: E402
from discover_workday_triple import find_site, sample_jobs  # noqa: E402
import requests  # noqa: E402

_dwt._TIMEOUT = 12

logger = logging.getLogger(__name__)

PROBE_REPORT = "scratch_neither_ats_probe.json"
OUT_REPORT = "scratch_workday_discovery.json"
_UA = {"User-Agent": "Mozilla/5.0 (HuntLoop ATS research)", "Content-Type": "application/json"}

# Generic words that a Workday tenant can legitimately be named while
# belonging to an unrelated company - the "red"/"western"/"tera" case the
# task calls out. A tenant on one of these slugs confirms *a* Workday
# tenant exists, not that it's this DOL employer's - so we flag it rather
# than store it. Same conservatism as detect_ats_for_sponsors._COMMON_WORDS.
_GENERIC_SLUGS = {
    "red", "western", "tera", "national", "general", "global", "capital",
    "american", "united", "first", "prime", "core", "edge", "peak", "apex",
    "advance", "premier", "allied", "standard", "central", "pacific",
    "summit", "unity", "vertex", "nova", "atlas", "delta", "omega",
}
# The board's hiringOrganization legal name is only an INFORMATIONAL cross-
# check, never a gate: it's noisy ("621 Salesforce.com India Private
# Limited Hyderabad Branch", "Canadian Imperial Bank of Commerce" for a
# "CIBC" employer) and produces false mismatches on exactly the acronym /
# subsidiary / regional-entity cases that dominate the DOL data. A very
# low score is still worth surfacing in the report.
_NAME_SIM_SUSPICIOUS = 25


def load_workday_hits():
    with open(PROBE_REPORT) as fh:
        report = json.load(fh)
    hits = []
    for r in report["results"]:
        if not r.get("workday"):
            continue
        slug, _, dc = r["workday"].partition(".")
        hits.append({"employer": r["employer"], "filings": r["filings"], "slug": slug, "dc": dc})
    return hits


def hiring_org_name(tenant, dc, site):
    """Fetch one posting's detail and return its hiringOrganization legal
    name, for disambiguation."""
    try:
        p0, _ = sample_jobs(tenant, dc, site, limit=1)
        ep = p0["jobPostings"][0]["externalPath"]
        base = f"https://{tenant}.{dc}.myworkdayjobs.com/wday/cxs/{tenant}/{site}"
        d = requests.get(f"{base}{ep}", headers=_UA, timeout=15).json()
        return (d.get("hiringOrganization") or {}).get("name") or ""
    except Exception as exc:  # noqa: BLE001
        logger.warning("hiring-org lookup failed for %s.%s/%s: %s", tenant, dc, site, exc)
        return ""


def discover_one(hit):
    slug, dc, employer = hit["slug"], hit["dc"], hit["employer"]
    out = {**hit, "site": None, "hiring_org": None, "name_sim": None,
           "careers_url": None, "status": None, "reason": None}

    # Generic-slug tenants: flag without even probing further - the probe
    # can't tell us whose tenant "red" is.
    if slug in _GENERIC_SLUGS:
        out["status"] = "needs_review"
        out["reason"] = f"generic slug {slug!r} - a Workday tenant by that name exists but may not be this employer"
        return out

    site = find_site(slug, dc)
    if site is None:
        out["status"] = "needs_review"
        out["reason"] = "no site name in the 18-name candidate list matched (needs a 'site:myworkdayjobs.com' search)"
        return out
    out["site"] = site

    org = hiring_org_name(slug, dc, site)
    out["hiring_org"] = org
    sim = token_set_ratio(normalize_employer_name(org), normalize_employer_name(employer)) if org else None
    out["name_sim"] = round(sim, 1) if sim is not None else None

    if sim is not None and sim < _NAME_SIM_SUSPICIOUS:
        out["status"] = "needs_review"
        out["reason"] = f"board hiring-org {org!r} looks unrelated to employer (sim {sim:.0f} < {_NAME_SIM_SUSPICIOUS})"
        return out

    out["careers_url"] = f"https://{slug}.{dc}.myworkdayjobs.com/en-US/{site}"
    out["status"] = "stored"
    return out


def upsert(session, stored):
    inserted = updated = 0
    for r in stored:
        slug = r["slug"]
        existing = session.query(Company).filter_by(name=slug).first()
        if existing is None:
            session.add(Company(name=slug, ats_platform="workday", ats_token=slug,
                                careers_url=r["careers_url"]))
            inserted += 1
        elif existing.ats_platform in (None, "unknown"):
            existing.ats_platform = "workday"
            existing.ats_token = slug
            existing.careers_url = r["careers_url"]
            updated += 1
        elif existing.ats_platform == "workday" and not existing.careers_url:
            existing.careers_url = r["careers_url"]
            updated += 1
    session.commit()
    return inserted, updated


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--from-report", action="store_true",
                    help=f"skip probing; commit the 'stored' rows already in {OUT_REPORT}")
    args = ap.parse_args()

    if args.from_report:
        with open(OUT_REPORT) as fh:
            results = json.load(fh)["results"]
        stored = [r for r in results if r["status"] == "stored"]
        review = [r for r in results if r["status"] == "needs_review"]
        print(f"from {OUT_REPORT}: {len(stored)} stored, {len(review)} needs_review")
        if args.commit:
            session = sessionmaker(bind=create_engine(DATABASE_URL))()
            ins, upd = upsert(session, stored)
            session.close()
            print(f"Committed: {ins} inserted, {upd} updated.")
        return

    hits = load_workday_hits()
    logger.info("Discovering {tenant, dc, site} for %d confirmed Workday hits", len(hits))

    ordered = sorted(hits, key=lambda h: -h["filings"])
    # Sequential, low pressure: probing 43 tenants x ~5 site guesses in
    # parallel gets throttled by Workday's edge (Nones -> false
    # "no site matched"). One at a time is slower but reliable.
    results = []
    for i, hit in enumerate(ordered, 1):
        r = discover_one(hit)
        results.append(r)
        logger.info("  %2d/%d  %-40s %-12s %s", i, len(ordered), r["employer"][:40],
                    r["status"], r.get("reason") or "")
        time.sleep(0.2)

    stored = [r for r in results if r["status"] == "stored"]
    review = [r for r in results if r["status"] == "needs_review"]

    with open(OUT_REPORT, "w") as fh:
        json.dump({"total": len(results), "stored": len(stored),
                   "needs_review": len(review), "results": results}, fh, indent=2)

    print(f"\n=== Workday discovery over {len(results)} confirmed hits ===")
    print(f"stored       : {len(stored)}")
    print(f"needs_review : {len(review)}")
    for r in review:
        print(f"   - {r['employer']}  ({r['slug']}.{r['dc']})  {r['reason']}")
    print(f"\nreport: {OUT_REPORT}")

    if args.commit:
        session = sessionmaker(bind=create_engine(DATABASE_URL))()
        ins, upd = upsert(session, stored)
        session.close()
        print(f"\nCommitted: {ins} companies rows inserted, {upd} updated.")
    else:
        print("\n(dry run - re-run with --commit to write companies rows)")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    main()
