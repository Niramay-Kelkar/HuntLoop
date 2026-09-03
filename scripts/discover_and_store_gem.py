"""
Confidence-gated onboarding of the LCA-verified Gem startup candidates
onto the Gem spider.

Input population: the 14 Gem-specific startup candidates sourced +
LCA-verified in the 2026-09-02 sourcing task, read from
``scratch_gem_startup_sponsor_candidates.json`` (``verdict`` ==
``verified`` only - this is a SEPARATE candidate population from the
Ashby startup list and is reused as-is here, not re-derived). Each is run
through the existing Gem resolver (``scripts/discover_gem_job_board.py``,
reused UNCHANGED) to find a live jobs.gem.com board, then a REAL gate is
applied before anything is written - mirroring the Ashby/SmartRecruiters/
iCIMS onboarding pattern exactly:

  * Gem confidence "high"  (a live board, >= 1 job, name plausibly
    matches the queried company)
        -> stored automatically
  * Gem confidence medium / low-suspect / found-unverifiable (a real
    board that currently lists 0 jobs)
        -> HELD (needs explicit human confirmation)
  * unresolved (no live board found under any candidate slug)
        -> skipped, logged - never stored

Every one of the 14 candidates already carries a "verified" LCA verdict
(this population has no "needs_review" case, unlike Ashby's Sierra/Basis
AI), so - unlike the Ashby gate - there is no separate LCA-verdict check
here; the gate is purely the Gem-resolution confidence.

Anything HELD is stored ONLY if its resolved slug appears in a
``--confirmations`` file (one slug per line, ``#`` comments ok). Nothing
gets ``companies.ats_platform = 'gem'`` (and therefore nothing gets
scraped by main.py / scripts/scrape_gem.py) without passing this.

Stored shape mirrors the Ashby/SmartRecruiters onboarding:
``companies.name`` == ``ats_token`` == the lower-cased jobs.gem.com slug;
``careers_url`` = ``https://jobs.gem.com/{slug}`` (not needed to scrape -
the slug alone suffices - but kept for parity/traceability).

Run:
    # dry run - writes the report, no DB writes
    PYTHONPATH=src python scripts/discover_and_store_gem.py

    # store the automatically-passing (high-confidence) matches
    PYTHONPATH=src python scripts/discover_and_store_gem.py --commit

    # also store the held slugs a human has explicitly approved
    PYTHONPATH=src python scripts/discover_and_store_gem.py \
        --commit --confirmations confirmed_gem_slugs.txt

Safe to re-run - only ever inserts a new row or fills a NULL/'unknown'
platform, never overwrites a different successful one.
"""
import argparse
import json
import logging
import sys

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

load_dotenv()

from huntloop.db_models import Company
from huntloop.settings import DATABASE_URL

sys.path.insert(0, "scripts")
import discover_gem_job_board as resolver  # noqa: E402  (reused unchanged)

logger = logging.getLogger(__name__)

CANDIDATES_PATH = "scratch_gem_startup_sponsor_candidates.json"
REPORT_PATH = "scratch_gem_onboarding.json"


def load_candidates() -> list[dict]:
    with open(CANDIDATES_PATH) as fh:
        rows = json.load(fh)["results"]
    out = []
    for r in rows:
        if r.get("verdict") == "verified":
            out.append({
                "name": r["name"],
                "lca_verdict": r["verdict"],
                "matched_employer": r.get("matched_employer_name_normalized"),
                "filing_count": r.get("filing_count"),
            })
    return out


def read_confirmations(path: str | None) -> tuple[set[str], dict[str, str]]:
    """Returns (approved_slugs, forced_slug_by_name) - same two-column
    format the Ashby onboarding script's confirmations file uses:
    ``slug`` alone, or ``Company Name<TAB>slug`` to also force a candidate
    slug into the resolver for a company whose real board isn't
    name-derivable."""
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


def gate(gem_confidence: str) -> str:
    """'auto' only for a genuinely high-confidence, non-empty Gem board;
    everything else that resolved (medium / low-suspect /
    found-unverifiable, including a real-but-currently-empty board) ->
    'held'. An unresolved candidate never reaches this function - see
    evaluate()."""
    return "auto" if gem_confidence == "high" else "held"


def evaluate(cand: dict, forced_slugs: dict[str, str] | None = None) -> dict:
    name = cand["name"]
    forced_slug = (forced_slugs or {}).get(name.lower())
    try:
        res = resolver.discover(name, forced_slug) if forced_slug else resolver.discover(name)
    except Exception as exc:
        logger.warning("Gem resolve failed for %r: %s", name, exc)
        return {**cand, "resolved_slug": None, "gem_confidence": "error",
                "job_count": 0, "board_org_name": None, "gate": "skip", "reason": str(exc)}

    slug = res.get("resolved_slug")
    if not slug:
        return {**cand, "resolved_slug": None, "gem_confidence": res.get("confidence", "unresolved"),
                "job_count": 0, "board_org_name": None, "gate": "skip",
                "reason": "no candidate slug returned a real jobs.gem.com board"}

    return {
        **cand,
        "resolved_slug": slug,
        "gem_confidence": res["confidence"],
        "job_count": res.get("job_count"),
        "board_org_name": res.get("org_name"),
        "name_similarity": res.get("name_similarity"),
        "winning_candidate_kind": res.get("winning_candidate_kind"),
        "method": res.get("method"),
        "gate": gate(res["confidence"]),
        "reason": None,
    }


def upsert(session, to_store: list[dict]) -> tuple[int, int]:
    inserted = updated = 0
    for r in to_store:
        slug = r["resolved_slug"].lower()
        careers_url = f"https://jobs.gem.com/{slug}"
        existing = session.query(Company).filter_by(name=slug).first()
        if existing is None:
            session.add(Company(
                name=slug, ats_platform="gem", ats_token=slug, careers_url=careers_url,
            ))
            inserted += 1
        elif existing.ats_platform in (None, "unknown"):
            existing.ats_platform = "gem"
            existing.ats_token = slug
            existing.careers_url = existing.careers_url or careers_url
            updated += 1
    session.commit()
    return inserted, updated


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--confirmations", help="file of human-approved jobs.gem.com slugs to store despite the gate")
    args = ap.parse_args()

    engine = create_engine(DATABASE_URL, echo=False)
    session = sessionmaker(bind=engine)()
    approved, forced_slugs = read_confirmations(args.confirmations)
    existing_names = {c.name for c in session.query(Company.name).all()}
    existing_platform = {c.name: c.ats_platform for c in session.query(Company.name, Company.ats_platform).all()}

    candidates = load_candidates()
    logger.info("Evaluating %d LCA-verified Gem startup candidates%s",
                len(candidates), f"; {len(approved)} human-approved slugs loaded" if approved else "")

    results = [evaluate(c, forced_slugs) for c in candidates]

    resolved = [r for r in results if r["resolved_slug"]]
    auto = [r for r in resolved if r["gate"] == "auto"]
    held = [r for r in resolved if r["gate"] == "held"]
    confirmed = [r for r in held if r["resolved_slug"].lower() in approved]
    blocked = [r for r in held if r["resolved_slug"].lower() not in approved]
    unresolved = [r for r in results if not r["resolved_slug"]]

    to_store, already = [], []
    for r in auto + confirmed:
        slug = r["resolved_slug"].lower()
        if slug in existing_names and existing_platform.get(slug) not in (None, "unknown"):
            already.append(r)
        else:
            to_store.append(r)

    report = {
        "population_size": len(candidates),
        "resolved": len(resolved),
        "gate_auto": len(auto),
        "gate_held": len(held),
        "gate_confirmed_by_human": len(confirmed),
        "gate_blocked": len(blocked),
        "unresolved": len(unresolved),
        "already_in_companies": len(already),
        "to_store": len(to_store),
        "results": sorted(results, key=lambda r: (r["gate"], -(r.get("filing_count") or 0))),
    }
    with open(REPORT_PATH, "w") as fh:
        json.dump(report, fh, indent=2, default=str)

    print(f"\n=== Gem onboarding over {len(candidates)} LCA-verified Gem startup candidates ===")
    print(f"resolved to a live Gem board              : {len(resolved)}")
    print(f"  gate PASS  (high confidence, auto)       : {len(auto)}")
    print(f"  gate HOLD  (needs a human)                : {len(held)}")
    print(f"     - explicitly confirmed this run        : {len(confirmed)}")
    print(f"     - still blocked (not confirmed)        : {len(blocked)}")
    print(f"unresolved (no live board found)           : {len(unresolved)}")
    print(f"already onboarded (skip)                   : {len(already)}")
    print(f"-> will store                               : {len(to_store)}")
    for r in sorted(results, key=lambda r: (r["gate"], r["name"])):
        print(f"  {r['name']:<20} slug={str(r['resolved_slug']):<22} "
              f"jobs={str(r.get('job_count')):<5} gem_conf={r['gem_confidence']:<55} gate={r['gate']}")
    print(f"\nreport: {REPORT_PATH}")

    if args.commit:
        ins, upd = upsert(session, to_store)
        print(f"\nCommitted: {ins} companies rows inserted, {upd} updated (NULL/'unknown' -> gem).")
    else:
        print("\n(dry run - re-run with --commit to write companies rows)")
    session.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    main()
