"""
Confidence-gated onboarding of the LCA-verified startup candidates onto
the Ashby spider.

Input population: the startup candidates sourced + LCA-verified in the
2026-09-01 sourcing tasks, read from ``scratch_startup_sponsor_candidates.json``
(``verdict`` in {``verified``, ``needs_review``}). Each is run through the
existing Ashby resolver (``scripts/discover_ashby_job_board.py``, reused
UNCHANGED) to find a live jobBoardName, then a REAL gate is applied before
anything is written:

  * Ashby confidence "high"  AND  the company's LCA verdict is "verified"
        -> stored automatically
  * Ashby confidence medium / low-suspect / found-unverifiable
        -> HELD (needs explicit human confirmation)
  * the company's LCA verdict is "needs_review" (Sierra, Basis AI)
        -> HELD regardless of Ashby confidence - a strong Ashby match
           does not upgrade an unverified sponsorship claim
  * unresolved (no live board)
        -> skipped, logged

Anything HELD is stored ONLY if its resolved jobBoardName appears in a
``--confirmations`` file (one slug per line, ``#`` comments ok). Nothing
gets ``companies.ats_platform = 'ashby'`` (and therefore nothing gets
scraped by main.py / scripts/scrape_ashby.py) without passing this.

Stored shape mirrors the SmartRecruiters onboarding + the pre-existing
``ramp`` row: ``companies.name`` == ``ats_token`` == the lower-cased
jobBoardName; ``careers_url`` = ``https://jobs.ashbyhq.com/{slug}`` (not
needed to scrape - the slug alone suffices - but kept for parity/traceability).

Run:
    # dry run - writes the report, no DB writes
    PYTHONPATH=src python scripts/discover_and_store_ashby.py

    # store the automatically-passing (high + LCA-verified) matches
    PYTHONPATH=src python scripts/discover_and_store_ashby.py --commit

    # also store the held slugs a human has explicitly approved
    PYTHONPATH=src python scripts/discover_and_store_ashby.py \
        --commit --confirmations confirmed_ashby_slugs.txt

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
import discover_ashby_job_board as resolver  # noqa: E402  (reused unchanged)

logger = logging.getLogger(__name__)

CANDIDATES_PATH = "scratch_startup_sponsor_candidates.json"
REPORT_PATH = "scratch_ashby_onboarding.json"


def load_candidates() -> list[dict]:
    with open(CANDIDATES_PATH) as fh:
        rows = json.load(fh)["results"]
    out = []
    for r in rows:
        if r.get("verdict") in ("verified", "needs_review"):
            out.append({
                "name": r["name"],
                "lca_verdict": r["verdict"],
                "matched_employer": r.get("matched_employer_name_normalized"),
                "filing_count": r.get("filing_count"),
            })
    return out


def read_confirmations(path: str | None) -> tuple[set[str], dict[str, str]]:
    """Returns (approved_slugs, forced_slug_by_name).

    Line formats (blank / '#' lines ignored):
      * ``slug``                - a bare human-approved jobBoardName (the
        original format; used for a board the resolver *did* find, just
        via a weak candidate kind - e.g. ``hex`` / ``ironcladhq``).
      * ``Company Name<TAB>slug`` - also supplies ``slug`` as a forced
        candidate for a company whose real board is NOT derivable from its
        name by any pattern (e.g. ``Anysphere<TAB>cursor``). The slug is
        fed to the UNCHANGED resolver's existing ``forced_slug`` argument
        and still goes through the exact same confidence gate.
    """
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


# Belt-and-braces, mirroring the SmartRecruiters gate's _NON_AUTO_KINDS: a
# board found only via a weak, collision-prone candidate never auto-passes
# even at "high" Ashby confidence - a name-similarity match on a slug that
# is just the first word, an acronym, or a "{name}hq"/"{name}careers"
# suffix variant is exactly the kind of thing to eyeball once.
_NON_AUTO_KINDS = {"suffix-variant", "first-word-only", "acronym"}


def gate(ashby_confidence: str, lca_verdict: str, winning_candidate_kind: str | None) -> str:
    """'auto' only for a high-confidence Ashby board reached via a strong
    candidate AND a fully LCA-verified sponsor; everything else that
    resolved -> 'held'."""
    if (
        ashby_confidence == "high"
        and lca_verdict == "verified"
        and winning_candidate_kind not in _NON_AUTO_KINDS
    ):
        return "auto"
    return "held"


def evaluate(cand: dict, forced_slugs: dict[str, str] | None = None) -> dict:
    name = cand["name"]
    forced_slug = (forced_slugs or {}).get(name.lower())
    try:
        res = resolver.discover(name, forced_slug) if forced_slug else resolver.discover(name)
    except Exception as exc:
        logger.warning("Ashby resolve failed for %r: %s", name, exc)
        return {**cand, "resolved_slug": None, "ashby_confidence": "error",
                "job_count": 0, "board_org_name": None, "gate": "skip", "reason": str(exc)}

    slug = res.get("resolved_slug")
    if not slug:
        return {**cand, "resolved_slug": None, "ashby_confidence": "unresolved",
                "job_count": 0, "board_org_name": None, "gate": "skip",
                "reason": "no candidate slug returned a live board"}

    return {
        **cand,
        "resolved_slug": slug,
        "ashby_confidence": res["confidence"],
        "job_count": res.get("job_count"),
        "board_org_name": res.get("board_org_name"),
        "name_similarity": res.get("name_similarity"),
        "winning_candidate_kind": res.get("winning_candidate_kind"),
        "method": res.get("method"),
        "gate": gate(res["confidence"], cand["lca_verdict"], res.get("winning_candidate_kind")),
        "reason": None,
    }


def upsert(session, to_store: list[dict]) -> tuple[int, int]:
    inserted = updated = 0
    for r in to_store:
        slug = r["resolved_slug"].lower()
        careers_url = f"https://jobs.ashbyhq.com/{slug}"
        # board_org_name was already fetched during evaluate()'s
        # confidence check (the same title-minus-" Jobs" extraction
        # huntloop.company_display_name.fetch_ashby_display_name does) -
        # no extra network call here.
        display_name = r.get("board_org_name")
        existing = session.query(Company).filter_by(name=slug).first()
        if existing is None:
            session.add(Company(
                name=slug, ats_platform="ashby", ats_token=slug, careers_url=careers_url,
                display_name=display_name,
            ))
            inserted += 1
        elif existing.ats_platform in (None, "unknown"):
            existing.ats_platform = "ashby"
            existing.ats_token = slug
            existing.careers_url = existing.careers_url or careers_url
            if display_name and not existing.display_name:
                existing.display_name = display_name
            updated += 1
    session.commit()
    return inserted, updated


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--confirmations", help="file of human-approved jobBoardName slugs to store despite the gate")
    args = ap.parse_args()

    engine = create_engine(DATABASE_URL, echo=False)
    session = sessionmaker(bind=engine)()
    approved, forced_slugs = read_confirmations(args.confirmations)
    existing_names = {c.name for c in session.query(Company.name).all()}
    existing_platform = {c.name: c.ats_platform for c in session.query(Company.name, Company.ats_platform).all()}

    candidates = load_candidates()
    logger.info("Evaluating %d LCA-verified startup candidates against Ashby%s",
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

    print(f"\n=== Ashby onboarding over {len(candidates)} LCA-verified startup candidates ===")
    print(f"resolved to a live Ashby board           : {len(resolved)}")
    print(f"  gate PASS  (high + LCA-verified, auto)  : {len(auto)}")
    print(f"  gate HOLD  (needs a human)              : {len(held)}")
    print(f"     - explicitly confirmed this run      : {len(confirmed)}")
    print(f"     - still blocked (not confirmed)      : {len(blocked)}")
    print(f"unresolved (no live board found)          : {len(unresolved)}")
    print(f"already onboarded (skip)                  : {len(already)}")
    print(f"-> will store                             : {len(to_store)}")
    for r in sorted(results, key=lambda r: (r["gate"], r["name"])):
        print(f"  {r['name']:<22} slug={str(r['resolved_slug']):<24} "
              f"jobs={str(r.get('job_count')):<5} lca={r['lca_verdict']:<12} "
              f"ashby_conf={r['ashby_confidence']:<45} gate={r['gate']}")
    print(f"\nreport: {REPORT_PATH}")

    if args.commit:
        ins, upd = upsert(session, to_store)
        print(f"\nCommitted: {ins} companies rows inserted, {upd} updated (NULL/'unknown' -> ashby).")
    else:
        print("\n(dry run - re-run with --commit to write companies rows)")
    session.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    main()
