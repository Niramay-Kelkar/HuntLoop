"""
Sourcing + LCA-verification step (NOT Ashby discovery, NOT a DB write) for
closing a real gap: the `companies` table only holds sponsors with
>= 20 real LCA filings (an ATS-matching-era cutoff, not a limit of the
underlying data), and Ashby is heavily used by smaller/startup employers
that cutoff structurally excludes.

This script does NOT guess or curate a "trustworthy startups" list on its
own authority - CANDIDATES below were assembled via real web searches (see
each entry's `source`) naming companies that actually use Ashby for
hiring, then every one is checked here against the FULL `lca_disclosures`
table (no >= 20 filter) using the exact same fuzzy-matching mechanism the
rest of this project already uses for DOL employer-name resolution
(`huntloop.matching.fuzzy_match.find_matching_employers` -
rapidfuzz `token_set_ratio`, threshold 88, `sponsor_name_overrides`
checked first). A candidate is only "verified" if that real match exists
- no LCA filing, no matter how well-known the company or how good an
Ashby candidate, means it stays unverified. This is sourcing/verification
only: it writes nothing to `companies`, and does not touch Ashby
discovery at all.

Run:
    PYTHONPATH=src python scripts/discover_startup_sponsors.py
    PYTHONPATH=src python scripts/discover_startup_sponsors.py --json out.json
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

# Candidate startups plausibly using Ashby for hiring, each with the real
# search/page that named it - assembled 2026-09-01, not guessed. Every
# name below actually appeared in one of these sources; several were
# cross-confirmed by their own real jobs.ashbyhq.com/{slug} URL turning up
# directly in search results (noted where that happened).
CANDIDATES: list[dict] = [
    # -- Ashby's own "companies using Ashby" marketing surface, reached via
    #    web search ("companies using Ashby ATS jobs.ashbyhq.com engineering
    #    startups" / "Ashby customers ... 'powered by Ashby' careers") and
    #    the aggregator jobspipe.dev/companies-using/ashby --
    {"name": "Notion", "source": "web search: Ashby customer list (ashbyhq.com/careers via search) + jobspipe.dev/companies-using/ashby"},
    {"name": "Linear", "source": "web search: Ashby customer list + jobspipe.dev/companies-using/ashby"},
    {"name": "Ramp", "source": "web search: Ashby customer list + jobspipe.dev/companies-using/ashby"},
    {"name": "Vanta", "source": "web search: Ashby customer list"},
    {"name": "OpenAI", "source": "jobspipe.dev/companies-using/ashby"},
    {"name": "Anysphere", "source": "jobspipe.dev/companies-using/ashby (listed as 'Cursor (Anysphere)')"},
    {"name": "Replit", "source": "jobspipe.dev/companies-using/ashby"},
    {"name": "Supabase", "source": "jobspipe.dev/companies-using/ashby"},
    {"name": "Docker", "source": "jobspipe.dev/companies-using/ashby"},
    {"name": "Modal", "source": "jobspipe.dev/companies-using/ashby"},
    {"name": "PostHog", "source": "jobspipe.dev/companies-using/ashby + web search Ashby customer list"},
    {"name": "Payabli", "source": "jobspipe.dev/companies-using/ashby"},
    {"name": "Substack", "source": "jobspipe.dev/companies-using/ashby"},
    {"name": "Mercury", "source": "jobspipe.dev/companies-using/ashby"},
    {"name": "Vercel", "source": "jobspipe.dev/companies-using/ashby + web search 'powered by Ashby' careers"},
    {"name": "Deel", "source": "jobspipe.dev/companies-using/ashby + web search Ashby customer list"},
    {"name": "Clerk", "source": "jobspipe.dev/companies-using/ashby"},
    {"name": "ElevenLabs", "source": "jobspipe.dev/companies-using/ashby"},
    {"name": "Sierra", "source": "jobspipe.dev/companies-using/ashby"},
    {"name": "LangChain", "source": "web search: Ashby customer list"},
    {"name": "Ironclad", "source": "web search: Ashby customer list"},
    {"name": "Lemonade", "source": "web search: Ashby customer list"},
    {"name": "Lime", "source": "web search: Ashby customer list"},
    {"name": "Gorgias", "source": "web search: Ashby customer list"},
    {"name": "UiPath", "source": "web search: Ashby customer list"},
    {"name": "Clay", "source": "web search: Ashby customer list"},
    {"name": "Harvey", "source": "web search: Ashby customer list"},
    {"name": "Deliveroo", "source": "web search: Ashby customer list"},
    {"name": "Zapier", "source": "web search: Ashby customer list"},
    {"name": "Retool", "source": "web search: Ashby customer list"},
    {"name": "Plaid", "source": "web search: 'powered by Ashby' careers list"},
    # -- Verified 2026-09-01 against the live Ashby posting API in the prior
    #    (Ashby discovery) task: real, non-empty jobs.ashbyhq.com boards --
    {"name": "Watershed", "source": "verified live jobs.ashbyhq.com/watershed board (prior Ashby-discovery task, 2026-09-01)"},
    {"name": "Baseten", "source": "verified live jobs.ashbyhq.com/baseten board (prior Ashby-discovery task, 2026-09-01)"},
    {"name": "Rentman", "source": "verified live jobs.ashbyhq.com/rentman board (prior Ashby-discovery task, 2026-09-01)"},
    {"name": "Sisense", "source": "verified live jobs.ashbyhq.com/sisense board (prior Ashby-discovery task, 2026-09-01)"},
    {"name": "Jiga", "source": "verified live jobs.ashbyhq.com/jiga board (prior Ashby-discovery task, 2026-09-01)"},
    {"name": "Superbolt", "source": "verified live jobs.ashbyhq.com/superbolt board (prior Ashby-discovery task, 2026-09-01)"},
    {"name": "Immunic Therapeutics", "source": "verified live jobs.ashbyhq.com/immunic-therapeutics board (prior Ashby-discovery task, 2026-09-01)"},
    {"name": "Agave", "source": "verified live jobs.ashbyhq.com/agave board (prior Ashby-discovery task, 2026-09-01)"},
    {"name": "Hex Technologies", "source": "verified live jobs.ashbyhq.com/hex board (prior Ashby-discovery task, 2026-09-01)"},
    # -- Found via targeted web searches for specific sectors, each
    #    confirmed by a real jobs.ashbyhq.com/{slug}/{id} posting URL
    #    appearing directly in the search results --
    {"name": "Middesk", "source": "web search 'fintech startup careers site:jobs.ashbyhq.com' -> jobs.ashbyhq.com/middesk posting"},
    {"name": "Clera", "source": "web search 'fintech startup careers site:jobs.ashbyhq.com' -> jobs.ashbyhq.com/Clera postings"},
    {"name": "insitro", "source": "web search 'biotech startup careers site:jobs.ashbyhq.com' -> jobs.ashbyhq.com/insitro posting"},
    {"name": "Semgrep", "source": "web search 'devtools infrastructure startup careers site:jobs.ashbyhq.com' -> jobs.ashbyhq.com/semgrep posting"},
    {"name": "Decagon", "source": "web search 'devtools infrastructure startup careers site:jobs.ashbyhq.com' -> jobs.ashbyhq.com/decagon posting"},
    {"name": "Firecrawl", "source": "web search 'devtools infrastructure startup careers site:jobs.ashbyhq.com' -> jobs.ashbyhq.com/firecrawl posting"},
    {"name": "Airwallex", "source": "web search 'devtools infrastructure startup careers site:jobs.ashbyhq.com' -> jobs.ashbyhq.com/airwallex board"},
    {"name": "Suno", "source": "web search 'devtools infrastructure startup careers site:jobs.ashbyhq.com' -> jobs.ashbyhq.com/suno board"},
    {"name": "Homebase", "source": "web search 'devtools infrastructure startup careers site:jobs.ashbyhq.com' -> jobs.ashbyhq.com/homebase posting"},
    {"name": "AgentMail", "source": "web search 'devtools infrastructure startup careers site:jobs.ashbyhq.com' -> jobs.ashbyhq.com/AgentMail posting"},
    {"name": "Basis AI", "source": "web search 'AI startup careers site:jobs.ashbyhq.com' -> jobs.ashbyhq.com/basis-ai board"},
    {"name": "Essential AI", "source": "web search 'AI startup careers site:jobs.ashbyhq.com' -> jobs.ashbyhq.com/essentialai board"},
    {"name": "Distyl AI", "source": "web search 'AI startup careers site:jobs.ashbyhq.com' -> jobs.ashbyhq.com/Distyl board"},
]


# Manual spot-check verdicts (2026-09-01), reached by pulling real
# job_title/worksite_city/worksite_state rows for each match and judging
# whether they plausibly belong to the queried company - not just trusting
# the fuzzy score. This is exactly the "residual ambiguity" fuzzy_match.py
# says threshold tuning alone can't eliminate (see its DEFAULT_THRESHOLD
# comment) - short, common-word company names ("Clay", "Harvey", "Linear",
# "Mercury", "Clerk", "Lemonade", "Lime", "Watershed", "Sierra",
# "Homebase") collided with unrelated, unrelated-sector real employers at
# score >= 88. None of these were added to sponsor_name_overrides (that
# table is for confirmed CORRECT mappings, not exclusions) - they are
# simply not treated as verified here.
_CONFIRMED_FALSE_POSITIVE = {
    # query name -> (wrong matched employer, why - real job_title/worksite evidence)
    "Lime": ("CARMEUSE LIME & STONE", "Pittsburgh-based industrial minerals firm (Business Analyst/SAP roles) - unrelated to Lime the micromobility company"),
    "Homebase": ("HOMECARE HOMEBASE", "Dallas-area home-health software vendor (Senior Software Engineer roles in NC/KY/TX) - a real but different company from the SF scheduling-app Homebase"),
    "Clera": ("CLEYRA", "Data engineering roles (Hartford/NYC/Irving) with no evident connection to Clera, the consumer fintech app"),
    "Watershed": ("MYSTIC RIVER WATERSHED ASSOCIATION", "Massachusetts environmental nonprofit (Stormwater Projects Manager) - unrelated to Watershed the climate-software startup"),
    "Immunic Therapeutics": ("C4 THERAPEUTICS", "Watertown MA biotech (Quality/Scientist roles) - a real but different biotech from NY-based Immunic Therapeutics"),
    "Linear": ("LINEAR DIMENSIONS SEMICONDUCTOR", "Houston mobile-developer role at an unrelated semiconductor firm - not Linear the SF software company"),
    "Clerk": ("CLERK OF THE CIRCUIT COURT OF VOLUSIA COUNTY", "a Florida county government office - obviously not Clerk (clerk.com)"),
    "Lemonade": ("ALEXS LEMONADE STAND FOUNDATION", "a childhood-cancer charity - not Lemonade Insurance"),
    "Clay": ("CLAY COUNTY HEALTHCARE AUTHORITY", "an Alabama county healthcare authority - not Clay the CRM startup"),
    "Harvey": ("GREGORY T HARVEY DMD", "an individual dentist's practice - not Harvey the legal-AI startup"),
    "Mercury": ("HDS MERCURY", "computer-vision/industrial-engineer roles (Jersey City/LA) - a logistics-automation employer, not Mercury the fintech bank"),
    # 2026-09-01 needs_review resolution: the fuzzy match "BASIS" ("Basis LLC",
    # 2 filings, "Strategic Business Design Manager", NYC, $122k) is NOT the
    # VC-backed AI-accounting startup Basis (getbasis.ai). A company that
    # raised $100M from Khosla/Accel/GV is a Delaware C-corp, never an "LLC";
    # "Strategic Business Design Manager" is not a role Basis hires (its
    # actual roles are "Member of Technical/Accounting Staff", "Deployed
    # Intelligence Strategist"); $122k is low for a funded AI startup; and NO
    # "Basis AI"/"Basis Technologies"/"Basis Platform"/"Basis, Inc." exists
    # anywhere in lca_disclosures. Only the NYC worksite lines up, and NYC is
    # a huge hub. -> confirmed false positive, stays excluded.
    "Basis AI": ("BASIS", "'Basis LLC' (2 filings, 'Strategic Business Design Manager', NYC, $122k) is not the VC-backed AI-accounting startup Basis - it's an LLC (Basis raised $100M and is a C-corp), the role isn't one Basis hires, and no 'Basis AI/Technologies/Platform' exists anywhere in lca_disclosures"),
}
# All 31 fuzzy matches accepted as "verified" have now been individually
# spot-checked against real job_title/worksite rows (batch 1 in the
# 2026-09-01 sourcing task; batch 2 - PLAID, UIPATH, RETOOL, AIRWALLEX US,
# REPLIT, SUBSTACK, VERCEL, DEEL, OPENAI, DOCKER, ELEVEN LABS, AGAVE TECH,
# SUPABASE, ESSENTIAL AI LABS - in the 2026-09-01 follow-up). Batch 2 found
# zero new false positives: every one had tech-sector job titles at a
# worksite consistent with the real company (e.g. REPLIT -> Foster City CA
# product/data roles; SUPABASE -> a "PostgreSQL Expert" role; VERCEL ->
# "DX Engineer, Frameworks"; OPENAI -> "Member of ... Staff" titles;
# DEEL -> Global Payroll GTM roles). See SESSIONS.md for the full per-match
# evidence table.
#
# Zapier and PostHog (both "no LCA match") were investigated directly, not
# accepted at face value: a full-table scan for any employer_name /
# employer_name_normalized / trade_name_dba containing "ZAPIER" or
# "POSTHOG" (and spacing/hyphen variants) returned nothing, and a
# low-threshold fuzzy pass surfaced only unrelated companies (ZPAPER 83,
# SHOP PO 71). Both are well-documented fully-remote/distributed employers
# that do not sponsor US work visas - a genuine true negative, not a
# matcher miss.

# Plausible but not independently confirmable from job-title/worksite data
# alone (no obvious mismatch, but no strong positive signal either) -
# reported separately, not folded into the "ready" list without a human
# glance.
# 2026-09-01: both prior needs_review entries were resolved with real
# job_title/worksite evidence.
#  * Sierra: the fuzzy hit "BLUE SIERRA" IS a false positive (a distinct SF
#    company, one generic SWE role), BUT the real Sierra AI (sierra.ai,
#    Bret Taylor / Clay Bavor) is a genuine, strongly-evidenced sponsor
#    filing as "Sierra Technologies, Inc." (14 certified filings: "Agent
#    Engineer" x3, "Research Engineer", "Engineer, Platform Engineering",
#    "Product Manager", "Commercial Counsel"; SF + NYC; $150k-310k). The
#    matcher missed it because token_set_ratio("SIERRA","SIERRA
#    TECHNOLOGIES") < 88. Fixed with a sponsor_name_overrides row
#    (sierra -> SIERRA TECHNOLOGIES) - the project's designated
#    manual-correction mechanism, same as the pre-existing kraken entry -
#    so find_matching_employers("Sierra") now resolves correctly and
#    Sierra's verdict is "verified".
#  * Basis AI: confirmed false positive (see _CONFIRMED_FALSE_POSITIVE).
_NEEDS_MANUAL_REVIEW: dict[str, tuple[str, str]] = {}


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
    print(f"=== {len(verified)} with real, spot-checked LCA sponsorship evidence (ready for Ashby discovery) ===")
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
