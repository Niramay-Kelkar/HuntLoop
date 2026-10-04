"""
Confidence-gated onboarding of DOL-sponsor companies onto the iCIMS
spider.

Population: every distinct DOL sponsor employer (>= --min-filings LCA
filings) that is NOT already in `companies` under any platform and was
NOT already resolved to Greenhouse/Lever (scratch_sponsor_ats_detection.json).
Each is run through the existing resolver
(scripts/discover_icims_job_board.py, reused UNCHANGED) to find a live
careers-{slug}.icims.com portal, then a REAL gate is applied:

  * confidence "high" via a strong candidate  AND robots-permitted
    AND currently lists >= 1 job                 -> stored automatically
  * live + robots-permitted + >=1 job but confidence medium / low-suspect
    / generic, or reached only via a first-word / acronym guess
                                                  -> HELD (needs a human)
  * robots.txt disallows /jobs/search             -> EXCLUDED, hard.
    Never stored, not even via --confirmations - a disallow is never
    bypassed (task requirement).
  * live portal but 0 open jobs / not an iCIMS portal / unresolved
                                                  -> EXCLUDED, logged

Anything HELD is stored ONLY if its resolved slug appears in a
--confirmations file (one slug per line, '#' comments ok;
'Company Name<TAB>slug' also supplies a forced candidate for a portal
whose slug isn't name-derivable). Nothing gets
`companies.ats_platform = 'icims'` - and therefore nothing gets scraped
by main.py / scripts/scrape_icims.py - without passing this.

Stored shape mirrors the Ashby / SmartRecruiters onboarding:
`companies.name` == `ats_token` == the careers-{slug} subdomain slug;
`careers_url` = `https://careers-{slug}.icims.com` (not needed to scrape,
kept for parity/traceability).

Run:
    # dry run - writes the report, no DB writes
    PYTHONPATH=src python scripts/discover_and_store_icims.py --limit 500

    # store the automatically-passing matches
    PYTHONPATH=src python scripts/discover_and_store_icims.py --limit 500 --commit

    # also store the held slugs a human has explicitly approved
    PYTHONPATH=src python scripts/discover_and_store_icims.py --limit 500 \
        --commit --confirmations confirmed_icims_slugs.txt

    # re-bucket / commit from a saved scan without re-hitting the network
    PYTHONPATH=src python scripts/discover_and_store_icims.py --from-report --commit

RE-RUN CADENCE: after each new quarterly DOL LCA file is ingested, same
as scripts/detect_ats_for_sponsors.py. Safe to re-run - only ever inserts
new rows or fills a NULL/'unknown' platform, never overwrites a different
successful one.
"""
import argparse
import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

load_dotenv()

from huntloop.db_models import Company
from huntloop.settings import DATABASE_URL

sys.path.insert(0, "scripts")
import discover_icims_job_board as resolver  # noqa: E402  (reused unchanged)

logger = logging.getLogger(__name__)

REPORT_PATH = "scratch_icims_onboarding.json"
GH_LEVER_REPORT = "scratch_sponsor_ats_detection.json"

_WEAK_KINDS = {"first-word-only", "acronym"}


def load_population(session, min_filings: int, limit: int | None):
    rows = session.execute(
        text(
            "SELECT employer_name_normalized AS employer, count(*) AS filings "
            "FROM lca_disclosures GROUP BY 1 HAVING count(*) >= :m ORDER BY filings DESC"
        ),
        {"m": min_filings},
    ).fetchall()

    try:
        with open(GH_LEVER_REPORT) as fh:
            gh_lever = {h["employer"] for h in json.load(fh).get("hits", [])}
    except FileNotFoundError:
        gh_lever = set()
        logger.warning("%s not found - not excluding known GH/Lever employers", GH_LEVER_REPORT)

    existing = {c.name for c in session.query(Company.name).all()}

    pop = [
        {"employer": r.employer, "filings": int(r.filings)}
        for r in rows
        if r.employer not in gh_lever
    ]
    if limit:
        pop = pop[:limit]
    return pop, existing


def read_confirmations(path: str | None) -> tuple[set[str], dict[str, str]]:
    if not path:
        return set(), {}
    approved: set[str] = set()
    forced: dict[str, str] = {}
    with open(path) as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            parts = [p.strip() for p in line.split("\t") if p.strip()]
            slug = parts[-1].lower()
            approved.add(slug)
            if len(parts) >= 2:
                forced[parts[0].lower()] = slug
    return approved, forced


def gate(res: dict) -> str:
    """'auto' | 'held' | 'excluded'."""
    if res.get("robots_ok") is False:
        return "excluded"                       # hard - a disallow is never bypassed
    if not res.get("is_portal") or not res.get("resolved_slug"):
        return "excluded"
    if res.get("job_count", 0) < 1:
        return "excluded"                       # live but nothing to scrape / unverifiable
    conf = res.get("confidence", "")
    kind = res.get("winning_candidate_kind")
    if conf == "high" and kind not in _WEAK_KINDS:
        return "auto"
    return "held"


def evaluate(cand: dict, forced_slugs: dict[str, str] | None = None) -> dict:
    name = cand["employer"]
    forced = (forced_slugs or {}).get(name.lower())
    try:
        res = resolver.discover(name, forced) if forced else resolver.discover(name)
    except Exception as exc:  # never let one bad row abort the pool
        logger.warning("iCIMS resolve failed for %r: %s", name, exc)
        return {**cand, "resolved_slug": None, "subdomain_slug": None, "robots_ok": None,
                "is_portal": False, "job_count": 0, "org_name": None, "confidence": "error",
                "winning_candidate_kind": None, "gate": "excluded", "reason": str(exc)}
    return {**cand, **res, "gate": gate(res), "reason": None}


def upsert(session, to_store: list[dict]) -> tuple[int, int]:
    inserted = updated = 0
    done: set[str] = set()
    for r in to_store:
        slug = r["subdomain_slug"].lower()
        if slug in done:
            continue
        done.add(slug)
        careers_url = f"https://careers-{slug}.icims.com"
        # org_name was already fetched during evaluate()'s confidence
        # check (the same title-minus-"Job Listings at " extraction
        # huntloop.company_display_name.fetch_icims_display_name does) -
        # no extra network call here.
        display_name = r.get("org_name")
        existing = session.query(Company).filter_by(name=slug).first()
        if existing is None:
            session.add(Company(
                name=slug, ats_platform="icims", ats_token=slug, careers_url=careers_url,
                display_name=display_name,
            ))
            inserted += 1
        elif existing.ats_platform in (None, "unknown"):
            existing.ats_platform = "icims"
            existing.ats_token = slug
            existing.careers_url = existing.careers_url or careers_url
            if display_name and not existing.display_name:
                existing.display_name = display_name
            updated += 1
    session.commit()
    return inserted, updated


def finish(session, results, approved, existing_names, min_filings, population_size, commit):
    resolved = [r for r in results if r["is_portal"]]
    auto = [r for r in results if r["gate"] == "auto"]
    held = [r for r in results if r["gate"] == "held"]
    confirmed = [r for r in held if (r.get("subdomain_slug") or "").lower() in approved]
    blocked = [r for r in held if (r.get("subdomain_slug") or "").lower() not in approved]
    robots_excluded = [r for r in results if r.get("robots_ok") is False]
    empty_excluded = [r for r in results if r["is_portal"] and r["job_count"] < 1 and r.get("robots_ok")]

    to_store, already = [], []
    seen_slugs: set[str] = set()
    for r in auto + confirmed:
        slug = (r.get("subdomain_slug") or "").lower()
        if not slug or slug in seen_slugs:
            continue  # two DOL employer names can share one real board
        seen_slugs.add(slug)
        if slug in existing_names:
            already.append(r)
        else:
            to_store.append(r)

    report = {
        "min_filings": min_filings,
        "population_size": population_size,
        "resolved_live_portal": len(resolved),
        "gate_auto": len(auto),
        "gate_held": len(held),
        "gate_held_confirmed": len(confirmed),
        "gate_held_still_blocked": len(blocked),
        "excluded_robots_disallow": len(robots_excluded),
        "excluded_empty_or_unverifiable": len(empty_excluded),
        "already_in_companies": len(already),
        "to_store": len(to_store),
        "results": sorted(results, key=lambda r: (r["gate"], -(r.get("filings") or 0))),
    }
    with open(REPORT_PATH, "w") as fh:
        json.dump(report, fh, indent=2, default=str)

    print(f"\n=== iCIMS onboarding over {population_size} DOL sponsors (>= {min_filings} filings) ===")
    print(f"resolved to a live iCIMS portal          : {len(resolved)}")
    print(f"  gate PASS  (high + strong + >=1 job)    : {len(auto)}")
    print(f"  gate HOLD  (needs a human)              : {len(held)}")
    print(f"     - explicitly confirmed this run      : {len(confirmed)}")
    print(f"     - still blocked (not confirmed)      : {len(blocked)}")
    print(f"EXCLUDED - robots.txt disallow (hard)     : {len(robots_excluded)}")
    print(f"EXCLUDED - live but 0 jobs / unverifiable : {len(empty_excluded)}")
    print(f"already onboarded (skip)                  : {len(already)}")
    print(f"-> will store                            : {len(to_store)}")
    for r in sorted([x for x in results if x["gate"] != "excluded" or x.get("robots_ok") is False],
                    key=lambda r: (r["gate"], r["employer"])):
        print(f"  {r['employer'][:32]:<32} slug={str(r.get('subdomain_slug')):<22} "
              f"robots_ok={str(r.get('robots_ok')):<5} jobs={str(r.get('job_count')):<4} "
              f"conf={str(r.get('confidence'))[:44]:<44} gate={r['gate']}")
    print(f"\nreport: {REPORT_PATH}")

    if commit:
        ins, upd = upsert(session, to_store)
        print(f"\nCommitted: {ins} companies rows inserted, {upd} updated (NULL/'unknown' -> icims).")
    else:
        print("\n(dry run - re-run with --commit to write companies rows)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--min-filings", type=int, default=20)
    ap.add_argument("--limit", type=int, default=None, help="cap the population (scoped run)")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--confirmations", help="file of human-approved careers-{slug} slugs to store despite the gate")
    ap.add_argument("--from-report", action="store_true",
                    help=f"skip the network scan; re-bucket + (with --commit) store from {REPORT_PATH}")
    args = ap.parse_args()

    session = sessionmaker(bind=create_engine(DATABASE_URL, echo=False))()
    approved, forced_slugs = read_confirmations(args.confirmations)
    existing_names = {c.name for c in session.query(Company.name).all()}

    if args.from_report:
        with open(REPORT_PATH) as fh:
            saved = json.load(fh)
        results = saved["results"]
        for r in results:
            r["gate"] = gate(r)
        finish(session, results, approved, existing_names,
               saved.get("min_filings", args.min_filings),
               saved.get("population_size", len(results)), args.commit)
        session.close()
        return

    population, _ = load_population(session, args.min_filings, args.limit)
    population = [p for p in population if p["employer"].lower() not in existing_names]
    logger.info("Evaluating %d DOL sponsors against iCIMS (%d workers)%s",
                len(population), args.workers,
                f"; {len(approved)} human-approved slugs loaded" if approved else "")

    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for i, res in enumerate(pool.map(lambda c: evaluate(c, forced_slugs), population), 1):
            results.append(res)
            if i % 25 == 0:
                logger.info("  ... %d/%d evaluated", i, len(population))

    finish(session, results, approved, existing_names, args.min_filings, len(population), args.commit)
    session.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    main()
