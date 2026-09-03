"""
Sourcing + LCA-verification step (NOT Gem discovery, NOT a DB write) for
closing the same real gap Ashby's startup-sourcing pass closed: the
`companies` table only holds sponsors with >= 20 real LCA filings (an
ATS-matching-era cutoff, not a limit of the underlying data), and Gem
(jobs.gem.com/{slug}) - like Ashby - is heavily used by smaller/startup
employers that cutoff structurally excludes. A recent discovery pass
(2026-09-02) confirmed Gem as a real ATS with a genuine public GraphQL
API, but found 0 companies in the `companies` table resolve to a Gem
board - the same startup-population gap Ashby had.

This is a SEPARATE candidate population from the Ashby sourcing pass
(`scripts/discover_startup_sponsors.py`) - the set of companies using
Gem for hiring is not the same set using Ashby, even though both skew
toward startups. This script does not touch that file or its output.

CANDIDATES below were assembled via real web searches (see each entry's
`source`) - `site:jobs.gem.com` searches that surface real board URLs
directly, Gem's own published case studies, and the 13 companies already
confirmed live in the prior Gem-discovery task. Every one is checked here
against the FULL `lca_disclosures` table (no >= 20 filter) using the
exact same fuzzy-matching mechanism the rest of this project already uses
for DOL employer-name resolution
(`huntloop.matching.fuzzy_match.find_matching_employers` - rapidfuzz
`token_set_ratio`, threshold 88, `sponsor_name_overrides` checked first).
A candidate is only "verified" if that real match exists AND survives an
individual job_title/worksite spot-check - no LCA filing, or a filing
that turns out to belong to an unrelated same-named company, means it
stays unverified. This is sourcing/verification only: it writes nothing
to `companies`, and does not run Gem discovery (jobs.gem.com board
checks) at all.

Run:
    PYTHONPATH=src python scripts/discover_gem_startup_sponsors.py
    PYTHONPATH=src python scripts/discover_gem_startup_sponsors.py --json out.json
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from huntloop.db_models import LcaDisclosure
from huntloop.matching.fuzzy_match import find_matching_employers
from huntloop.settings import DATABASE_URL

# Candidate startups plausibly using Gem for hiring, each with the real
# search/page that named it - assembled 2026-09-02, not guessed.
CANDIDATES: list[dict] = [
    # -- The 13 companies already confirmed live in the prior Gem-discovery
    #    task (2026-09-01/02, real jobs.gem.com/{slug} API responses) --
    {"name": "Modular", "source": "confirmed live jobs.gem.com/modular board (prior Gem-discovery task)"},
    {"name": "Linktree", "source": "confirmed live jobs.gem.com/linktree board (prior Gem-discovery task)"},
    {"name": "Paces", "source": "confirmed live jobs.gem.com/paces board (prior Gem-discovery task)"},
    {"name": "Nominal", "source": "confirmed live jobs.gem.com/nominal board (prior Gem-discovery task)"},
    {"name": "ntop", "source": "confirmed live jobs.gem.com/ntop board (prior Gem-discovery task)"},
    {"name": "Instrumental", "source": "confirmed live jobs.gem.com/instrumental-inc- board (prior Gem-discovery task)"},
    {"name": "Function Health", "source": "confirmed live jobs.gem.com/function-health board (prior Gem-discovery task)"},
    {"name": "Blue J", "source": "confirmed live jobs.gem.com/blue-j board (prior Gem-discovery task)"},
    {"name": "Apartment List", "source": "confirmed live jobs.gem.com/apartment-list board (prior Gem-discovery task)"},
    {"name": "SkillsJump", "source": "confirmed live jobs.gem.com/skillsjump board (prior Gem-discovery task)"},
    {"name": "Planned", "source": "confirmed live jobs.gem.com/planned board (prior Gem-discovery task)"},
    {"name": "Felix Technologies", "source": "confirmed live jobs.gem.com/felix board (prior Gem-discovery task)"},
    {"name": "Gem", "source": "confirmed live jobs.gem.com/gem board (Gem's own board, prior Gem-discovery task)"},
    # -- New this task: web search `site:jobs.gem.com` surfacing real board
    #    URLs directly (2026-09-02) --
    {"name": "Retool", "source": "web search site:jobs.gem.com -> jobs.gem.com/retool board"},
    {"name": "Quo", "source": "web search site:jobs.gem.com -> jobs.gem.com/quo board"},
    {"name": "Rivia", "source": "web search site:jobs.gem.com -> jobs.gem.com/rivia board"},
    {"name": "Elloe AI", "source": "web search site:jobs.gem.com -> jobs.gem.com/elloe-ai board"},
    {"name": "Deepline", "source": "web search site:jobs.gem.com -> jobs.gem.com/deepline-com posting"},
    {"name": "Luma AI", "source": "web search site:jobs.gem.com -> jobs.gem.com/lumalabs-ai board"},
    {"name": "Bohler", "source": "web search site:jobs.gem.com -> jobs.gem.com/bohler- posting"},
    {"name": "Eliza", "source": "web search site:jobs.gem.com -> jobs.gem.com/eliza board"},
    {"name": "Agora", "source": "web search site:jobs.gem.com -> jobs.gem.com/agora board"},
    {"name": "Emerge Career", "source": "web search site:jobs.gem.com -> jobs.gem.com/emerge-career board"},
    {"name": "Protege AI", "source": "web search site:jobs.gem.com -> jobs.gem.com/protegeai posting"},
    {"name": "GC AI", "source": "web search site:jobs.gem.com -> jobs.gem.com/gc-ai posting"},
    {"name": "Jetty", "source": "web search site:jobs.gem.com -> jobs.gem.com/jetty-careers posting"},
    {"name": "Bolna AI", "source": "web search site:jobs.gem.com -> jobs.gem.com/bolna board"},
    {"name": "Myriad Technology", "source": "web search site:jobs.gem.com -> jobs.gem.com/myriad-technology posting"},
    {"name": "QuestDB", "source": "web search site:jobs.gem.com -> jobs.gem.com/questdb-com board"},
    {"name": "Kyth", "source": "web search site:jobs.gem.com -> jobs.gem.com/kythhire-com board"},
    {"name": "Yeet", "source": "web search site:jobs.gem.com -> jobs.gem.com/yeet board"},
    {"name": "Epoch Blue", "source": "web search site:jobs.gem.com -> jobs.gem.com/epoch-blue board"},
    {"name": "Tokaido Health", "source": "web search site:jobs.gem.com -> jobs.gem.com/tokaidohealth board"},
    {"name": "Cactus Club", "source": "web search site:jobs.gem.com -> jobs.gem.com/cactusclub board"},
    {"name": "Constellation Institute", "source": "web search site:jobs.gem.com -> jobs.gem.com/constellation-institute posting"},
    {"name": "HASH", "source": "web search site:jobs.gem.com -> jobs.gem.com/hash posting"},
    {"name": "CareTria", "source": "web search site:jobs.gem.com -> jobs.gem.com/caretria board"},
    {"name": "Nuvo", "source": "web search site:jobs.gem.com -> jobs.gem.com/nuvo posting"},
    {"name": "Letter AI", "source": "web search site:jobs.gem.com -> jobs.gem.com/letter-ai posting"},
    # -- Gem's own published case studies (gem.com/case-study/...), naming
    #    real customers using Gem for hiring --
    {"name": "Scale AI", "source": "gem.com/case-study/scaleai (Gem's own published customer case study)"},
    {"name": "Veho Technologies", "source": "gem.com/case-study/veho + jobs.techstars.com/companies/veho postings (Gem case study + real posting URLs)"},
]


# Manual spot-check verdicts (2026-09-02), reached by pulling real
# job_title/worksite_city/worksite_state rows for each match and judging
# whether they plausibly belong to the queried company - not just trusting
# the fuzzy score. This mirrors the Ashby sourcing pass's exact method:
# short/generic/common-word company names collide with unrelated
# same-named real employers at score >= 88. None of these are added to
# sponsor_name_overrides (that table is for confirmed CORRECT mappings,
# not exclusions).
#
# Two real sponsor_name_overrides rows were added this task (the
# project's designated manual-correction mechanism, same as the
# pre-existing kraken/sierra entries):
#   * modular -> MODULAR (18 filings: Machine Learning Compiler Engineer,
#     AI GPU Performance Engineer, AI Compiler Engineer - Mountain
#     View/Austin, an exact match to Modular's real jobs.gem.com postings
#     "Libraries Engineer"/"Cloud Inference Engineer"/"AI Kernel Engineer").
#     find_matching_employers("Modular") was returning "ADVANCED MODULAR
#     SYSTEMS" (1 filing, an unrelated Goleta CA equipment-engineering
#     firm) as its top pick even though the exact name "MODULAR" (18
#     filings) also exists and scores 100 via token_set_ratio - a
#     tie-break/ranking artifact of the matcher, not a threshold miss.
#     The override forces the real, evidenced match.
#   * ntop -> NTOPOLOGY (15 filings: Geometry Software Engineer, Product
#     Manager (Simulation and Optimization), NYC - matches nTop's real
#     3D-engineering-software product and its real jobs.gem.com postings
#     "Field Solutions Engineer"/"Software Engineer (L4), Build Team").
#     Exactly the Sierra pattern: token_set_ratio("NTOP","NTOPOLOGY") =
#     61.5, below the 88 threshold, so find_matching_employers("ntop")
#     returned nothing at all until this override was added - the
#     company's real legal/product name (nTopology, since rebranded to
#     nTop) diverges too far from the short brand name for fuzzy matching
#     alone to find it.
_CONFIRMED_FALSE_POSITIVE: dict[str, tuple[str, str]] = {
    "Rivia": ("RIVIAH", "Rehab Specialist / Physical Therapist roles in Hicksville/Deer Park NY - a home-health/rehab company, unrelated to Rivia (an engineering-heavy startup per its real jobs.gem.com posting titles); no exact 'RIVIA' filing exists"),
    "Planned": ("FLORIDA PLANNED CABINETS", "Orlando FL Sales Manager at an hourly wage - a cabinet/furniture company, unrelated to Planned's startup job board; no exact 'PLANNED' filing exists"),
    "Gem": ("BEAUTY GEM", "New York Supply Chain Analyst at an hourly wage - a beauty-supply company, unrelated to Gem the recruiting-software company; no exact 'GEM' filing exists"),
    "Agora": ("AGORA ATLAS", "Agora Atlas Corp is a real, separately-funded ($57M+ raised) Jersey City SaaS startup founded 2023 (confirmed via SEC Form D filings), a coincidental name collision with whichever 'Agora' hosts the Gem board; no positive evidence ties the two, and 'AGORA' alone has no exact filing"),
    "Constellation Institute": ("CONSTELLATION", "Edina MN Sr. Product Engineer role at a single-filing 'CONSTELLATION' employer with no identifiable connection to Constellation Institute; 'Constellation' is a widely-reused generic company name (Constellation Energy, Constellation Brands, Constellation Software, etc.)"),
    "HASH": ("AQUILA HASH", "San Jose Supply Chain Analyst role - reads like a crypto-mining/hash-power hardware employer, unrelated to the Gem-hosted HASH board; no exact 'HASH' filing exists"),
    "Veho Technologies": ("X TECHNOLOGIES", "Irving TX Software Developer role - the match score (92.3) is a token_set_ratio artifact from the shared word 'TECHNOLOGIES' only; the distinctive token 'VEHO' does not appear anywhere in 'X TECHNOLOGIES', and no exact 'VEHO' filing exists"),
}

# Plausible but not independently confirmable from job-title/worksite data
# alone - reported separately, not folded into the "ready" list without a
# human glance.
_NEEDS_MANUAL_REVIEW: dict[str, tuple[str, str]] = {
    "Function Health": ("FUNCTION", "Only 2 filings, both New York-based Senior Backend Engineer / R&D Manager roles; public sources place Function Health's HQ in San Francisco/Austin, not NYC, and 'Function' alone is too generic a name to confirm or rule out from job-title/worksite data alone - no exact 'FUNCTION HEALTH' filing exists to settle it either way"),
}


def filing_count(session: Session, employer_name_normalized: str) -> int:
    return session.execute(
        select(func.count(LcaDisclosure.id)).where(
            LcaDisclosure.employer_name_normalized == employer_name_normalized
        )
    ).scalar_one()


def verify_all(session: Session) -> list[dict]:
    results = []
    for cand in CANDIDATES:
        name = cand["name"]
        matches = find_matching_employers(session, name)  # default threshold=88, no >=20 filter anywhere
        if matches:
            best = matches[0]
            verdict = "verified"
            note = None
            if name in _CONFIRMED_FALSE_POSITIVE:
                wrong_employer, why = _CONFIRMED_FALSE_POSITIVE[name]
                if best.employer_name_normalized == wrong_employer:
                    verdict = "false_positive"
                    note = why
            elif name in _NEEDS_MANUAL_REVIEW:
                review_employer, why = _NEEDS_MANUAL_REVIEW[name]
                if best.employer_name_normalized == review_employer:
                    verdict = "needs_review"
                    note = why
            results.append({
                "name": name,
                "source": cand["source"],
                "matched": True,
                "verdict": verdict,
                "note": note,
                "matched_employer_name_normalized": best.employer_name_normalized,
                "match_score": best.score,
                "match_source": best.source,  # "override" or "fuzzy"
                "filing_count": filing_count(session, best.employer_name_normalized),
                "other_candidates": [
                    {"name": m.employer_name_normalized, "score": m.score}
                    for m in matches[1:5]
                ],
            })
        else:
            results.append({
                "name": name,
                "source": cand["source"],
                "matched": False,
            })
    return results


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", dest="json_out", help="write full results here")
    args = ap.parse_args()

    engine = create_engine(DATABASE_URL, echo=False)
    with Session(engine) as session:
        results = verify_all(session)

    verified = [r for r in results if r.get("verdict") == "verified"]
    false_pos = [r for r in results if r.get("verdict") == "false_positive"]
    needs_review = [r for r in results if r.get("verdict") == "needs_review"]
    unmatched = [r for r in results if not r["matched"]]

    print(f"Checked {len(results)} candidates against the full lca_disclosures table "
          f"(no >= 20-filing floor).\n")
    print(f"=== {len(verified)} with real, spot-checked LCA sponsorship evidence (ready for Gem discovery) ===")
    for r in sorted(verified, key=lambda r: -r["filing_count"]):
        print(f"  {r['name']:<24} -> {r['matched_employer_name_normalized']!r:<35} "
              f"filings={r['filing_count']:<5} score={r['match_score']:.1f}")

    print(f"\n=== {len(false_pos)} raw fuzzy hits REJECTED as confirmed false positives (spot-check found a different real company) ===")
    for r in false_pos:
        print(f"  {r['name']:<24} -> {r['matched_employer_name_normalized']!r}")
        print(f"      {r['note']}")

    print(f"\n=== {len(needs_review)} raw fuzzy hits flagged for manual review (plausible but unconfirmed) ===")
    for r in needs_review:
        print(f"  {r['name']:<24} -> {r['matched_employer_name_normalized']!r}")
        print(f"      {r['note']}")

    print(f"\n=== {len(unmatched)} with NO LCA sponsorship evidence found at all ===")
    for r in unmatched:
        print(f"  {r['name']}")

    print(f"\n{len(verified)}/{len(results)} candidates carry real, spot-checked LCA sponsorship evidence "
          f"({len(verified) / len(results) * 100:.1f}%) - "
          f"{len(false_pos)} more matched but were rejected as false positives, "
          f"{len(needs_review)} need a human glance, "
          f"{len(unmatched)} had no LCA match at all.")

    if args.json_out:
        with open(args.json_out, "w") as fh:
            json.dump({"results": results}, fh, indent=2, default=str)
        print(f"report: {args.json_out}")


if __name__ == "__main__":
    sys.exit(main())
