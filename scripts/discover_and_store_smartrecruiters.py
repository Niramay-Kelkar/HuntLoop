"""
Confidence-gated onboarding of DOL-sponsor companies onto the
SmartRecruiters spider.

For every distinct DOL sponsor employer (>= --min-filings LCA filings)
that is NOT already matched to Greenhouse / Lever / Workday, this runs
the existing resolver (scripts/discover_smartrecruiters_id.py, reused
unchanged) to find a live SmartRecruiters companyId, then applies a REAL
gate before anything is written:

  * confidence "high" AND a non-collision candidate matched
                                      -> stored automatically
  * confidence "high" but only a `name+2` / `name+Inc` collision-suffix
    candidate matched                 -> NOT stored (see below)
  * confidence "medium" / "low-*"     -> NOT stored
  * unresolved                        -> skipped, logged.

Anything not auto-stored goes to the report's `blocked` list and is
stored ONLY if its resolved id appears in a --confirmations file (an
explicit human approval).

Why the collision-suffix carve-out: SmartRecruiters only issues a
`{name}2` / `{name}Inc` companyId when the plain `{name}` is already
taken. A large enterprise (Apple, Accenture, Goldman Sachs, ...) that
turns up ONLY at `apple2` / `accenture1` is, in practice, an abandoned
free-trial tenant from years ago - the resolver's name-similarity check
still says "high" because whoever created the trial named the board
correctly. This treats a `suffix-variant` win exactly the way the
resolver itself already treats its own weak `first-word-only` / `acronym`
candidates ("loose - confirm"), just applied in the onboarding gate
rather than the resolver (which is reused unchanged). Real cases that
genuinely live at a suffixed id (`ubisoft2`, `atos1`) are one line in a
--confirmations file.

The gate is enforced here, not merely documented: with --commit, a
non-"high" result is written ONLY when --confirmations lists its id.
Nothing gets `companies.ats_platform = 'smartrecruiters'` (and therefore
nothing gets scraped by main.py) without passing this.

SmartRecruiters companyId lookup is case-insensitive (verified against
the live API), so ids are stored lower-cased to match the project's
`companies.name` == `ats_token` convention.

Run:
    # dry run - writes scratch_smartrecruiters_discovery.json, no DB writes
    PYTHONPATH=src python scripts/discover_and_store_smartrecruiters.py

    # store the automatically-passing (high-confidence) matches
    PYTHONPATH=src python scripts/discover_and_store_smartrecruiters.py --commit

    # also store the medium/low ids a human has explicitly approved
    PYTHONPATH=src python scripts/discover_and_store_smartrecruiters.py \
        --commit --confirmations confirmed_sr_ids.txt

`--confirmations` file: one approved companyId per line (blank lines and
`#` comments ignored); `employer<TAB>companyId` is also accepted.

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
import discover_smartrecruiters_id as resolver  # noqa: E402  (reused unchanged)

logger = logging.getLogger(__name__)

REPORT_PATH = "scratch_smartrecruiters_discovery.json"
GH_LEVER_REPORT = "scratch_sponsor_ats_detection.json"
DEFAULT_MIN_FILINGS = 20
_MAX_WORKERS = 40


# A win on one of these candidate kinds never auto-passes the gate even at
# "high" resolver confidence - it needs an explicit human confirmation.
# `suffix-variant` (name+2 / name+Inc) -> almost always an abandoned
# free-trial tenant for a big enterprise; the resolver's other weak kinds
# it already labels "loose - confirm" itself but we belt-and-brace here.
_NON_AUTO_KINDS = {"suffix-variant", "first-word-only", "acronym"}


def gate(confidence: str, winning_candidate_kind: str | None) -> str:
    """'auto' only for a high-confidence resolution reached via a
    non-collision candidate; everything else -> 'needs_confirmation'."""
    if confidence == "high" and winning_candidate_kind not in _NON_AUTO_KINDS:
        return "auto"
    return "needs_confirmation"


def load_population(session, min_filings: int, limit: int | None):
    """Distinct DOL sponsor employers with >= min_filings filings, minus
    the ones already resolved to Greenhouse/Lever (scratch report) and
    minus anything already in `companies` under any platform."""
    rows = session.execute(
        text(
            "SELECT employer_name_normalized AS employer, count(*) AS filings "
            "FROM lca_disclosures GROUP BY 1 HAVING count(*) >= :m ORDER BY filings DESC"
        ),
        {"m": min_filings},
    ).fetchall()

    try:
        with open(GH_LEVER_REPORT) as fh:
            gh_lever_employers = {h["employer"] for h in json.load(fh).get("hits", [])}
    except FileNotFoundError:
        gh_lever_employers = set()
        logger.warning("%s not found - not excluding known GH/Lever employers", GH_LEVER_REPORT)

    existing_company_names = {c.name for c in session.query(Company.name).all()}

    pop = [
        {"employer": r.employer, "filings": int(r.filings)}
        for r in rows
        if r.employer not in gh_lever_employers
    ]
    if limit:
        pop = pop[:limit]
    return pop, gh_lever_employers, existing_company_names


def read_confirmations(path: str | None) -> set[str]:
    if not path:
        return set()
    approved: set[str] = set()
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            approved.add(line.split("\t")[-1].strip().lower())
    return approved


def evaluate(employer: dict) -> dict:
    """Resolve one employer name -> gated result dict. Network only, no DB."""
    name = employer["employer"]
    try:
        res = resolver.discover(name)
    except Exception as exc:  # never let one bad row abort the pool
        logger.warning("resolve failed for %r: %s", name, exc)
        return {**employer, "resolved_id": None, "confidence": "error",
                "total_found": 0, "board_name": None, "gate": "skip", "reason": str(exc)}

    rid = res.get("resolved_id")
    if not rid:
        return {**employer, "resolved_id": None, "confidence": "unresolved",
                "total_found": 0, "board_name": None, "gate": "skip",
                "reason": "no candidate returned a live board"}

    kind = res.get("winning_candidate_kind")
    return {
        **employer,
        "resolved_id": rid,
        "resolved_id_stored": rid.lower(),
        "confidence": res["confidence"],
        "total_found": res["total_found"],
        "board_name": res.get("board_name"),
        "name_similarity": res.get("name_similarity"),
        "winning_candidate_kind": kind,
        "gate": gate(res["confidence"], kind),
        "reason": None,
    }


def upsert(session, to_store: list[dict]) -> tuple[int, int]:
    inserted = updated = 0
    for r in to_store:
        cid = r["resolved_id_stored"]
        existing = session.query(Company).filter_by(name=cid).first()
        if existing is None:
            session.add(Company(name=cid, ats_platform="smartrecruiters", ats_token=cid))
            inserted += 1
        elif existing.ats_platform in (None, "unknown"):
            existing.ats_platform = "smartrecruiters"
            existing.ats_token = cid
            updated += 1
    session.commit()
    return inserted, updated


def finish(session, results: list[dict], approved: set[str], existing_names: set[str],
           min_filings: int, population_size: int, commit: bool) -> None:
    """Bucket `results` against the confidence gate + human confirmations,
    (re)write the report, print the breakdown, and optionally commit."""
    resolved = [r for r in results if r["resolved_id"]]
    auto = [r for r in resolved if r["gate"] == "auto"]
    needs_conf = [r for r in resolved if r["gate"] == "needs_confirmation"]
    confirmed = [r for r in needs_conf if r["resolved_id_stored"] in approved]
    blocked = [r for r in needs_conf if r["resolved_id_stored"] not in approved]
    unresolved = [r for r in results if not r["resolved_id"]]

    # Don't re-store an id that's already a companies row.
    to_store = [r for r in (auto + confirmed) if r["resolved_id_stored"] not in existing_names]
    already = [r for r in (auto + confirmed) if r["resolved_id_stored"] in existing_names]

    report = {
        "min_filings": min_filings,
        "population_size": population_size,
        "resolved": len(resolved),
        "gate_auto_high": len(auto),
        "gate_needs_confirmation": len(needs_conf),
        "gate_confirmed_by_human": len(confirmed),
        "gate_blocked": len(blocked),
        "unresolved": len(unresolved),
        "already_in_companies": len(already),
        "to_store": len(to_store),
        "results": sorted(results, key=lambda r: -r["filings"]),
    }
    with open(REPORT_PATH, "w") as fh:
        json.dump(report, fh, indent=2)

    print(f"\n=== SmartRecruiters onboarding over {population_size} DOL sponsors (>= {min_filings} filings) ===")
    print(f"resolved to a live companyId : {len(resolved)}")
    print(f"  gate PASS  (high confidence, auto)      : {len(auto)}")
    print(f"  gate HOLD  (medium/low, needs a human)  : {len(needs_conf)}")
    print(f"     - explicitly confirmed this run      : {len(confirmed)}")
    print(f"     - still blocked (not confirmed)      : {len(blocked)}")
    print(f"unresolved (no live board found)          : {len(unresolved)}")
    print(f"already in companies (skip)               : {len(already)}")
    print(f"-> will store                             : {len(to_store)}")
    if blocked:
        print("\n  blocked (medium/low confidence, not stored - confirm explicitly to onboard):")
        for r in sorted(blocked, key=lambda r: -r["total_found"])[:25]:
            print(f"    {r['employer']!r} -> {r['resolved_id']!r} "
                  f"(board {r['board_name']!r}, {r['total_found']} postings, {r['confidence']})")
    print(f"\nreport: {REPORT_PATH}")

    if commit:
        ins, upd = upsert(session, to_store)
        print(f"\nCommitted: {ins} companies rows inserted, {upd} updated (NULL/'unknown' -> smartrecruiters).")
    else:
        print("\n(dry run - re-run with --commit to write companies rows)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--min-filings", type=int, default=DEFAULT_MIN_FILINGS)
    ap.add_argument("--limit", type=int, default=None, help="cap the population (for a scoped run)")
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--confirmations", help="file of human-approved companyIds to store despite medium/low confidence")
    ap.add_argument("--from-report", action="store_true",
                    help=f"skip the network scan; re-bucket + (with --commit) store from an existing {REPORT_PATH}")
    args = ap.parse_args()

    engine = create_engine(DATABASE_URL, echo=False)
    session = sessionmaker(bind=engine)()
    approved = read_confirmations(args.confirmations)
    existing_names = {c.name for c in session.query(Company.name).all()}

    if args.from_report:
        with open(REPORT_PATH) as fh:
            saved = json.load(fh)
        results = saved["results"]
        # Older resolved rows may predate resolved_id_stored / gate keys.
        for r in results:
            if r.get("resolved_id") and "resolved_id_stored" not in r:
                r["resolved_id_stored"] = r["resolved_id"].lower()
            if r.get("resolved_id") and "gate" not in r:
                r["gate"] = gate(r.get("confidence", ""), r.get("winning_candidate_kind"))
        logger.info("Re-bucketing %d results from %s; %d human-approved ids loaded",
                    len(results), REPORT_PATH, len(approved))
        finish(session, results, approved, existing_names,
               saved.get("min_filings", args.min_filings), saved.get("population_size", len(results)),
               args.commit)
        session.close()
        return

    population, gh_lever, _ = load_population(session, args.min_filings, args.limit)
    logger.info(
        "Evaluating %d DOL sponsors (>= %d filings, minus %d GH/Lever, minus %d existing companies rows)%s",
        len(population), args.min_filings, len(gh_lever), len(existing_names),
        f"; {len(approved)} human-approved ids loaded" if approved else "",
    )

    results = []
    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        for i, res in enumerate(pool.map(evaluate, population), 1):
            results.append(res)
            if i % 250 == 0:
                logger.info("  ... %d/%d evaluated", i, len(population))

    finish(session, results, approved, existing_names, args.min_filings, len(population), args.commit)
    session.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    main()
