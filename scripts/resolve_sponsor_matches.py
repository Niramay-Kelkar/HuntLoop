"""
One-off script: resolve and persist each companies row's DOL sponsor
employer name into companies.matched_sponsor_employer_name.

Reuses huntloop.matching.fuzzy_match.find_matching_employers() unchanged
(Phase 1/2's matching engine - see CLAUDE.md's architectural decisions on
sponsor_name_overrides/fuzzy matching) - this script does not reimplement
any matching logic, it just calls that function for every current company
and stores the top-scoring result. sponsor_name_overrides entries (e.g.
Kraken) still take precedence, since find_matching_employers() checks that
table first and short-circuits to it before fuzzy matching runs at all.

Companies with no match clearing DEFAULT_THRESHOLD are left NULL, not
forced to a low-confidence guess - find_matching_employers() returns an
empty list in that case, same behavior as get_sponsorship_summary()'s
existing "no match" path.

Not part of the app's ongoing pipeline - run manually via:

    python scripts/resolve_sponsor_matches.py

Safe to re-run: it recomputes and overwrites every company's value each
time, so a re-run after sponsor_name_overrides changes (e.g. a new
override added) or after more LCA data is ingested will pick up the
updated match.

Also stamps `companies.sponsor_checked_at` with the current time for
every row it processes, whether or not a match is found - this is what
distinguishes "checked, no match" from "never checked" downstream (see
migration d4e5f6a7b8c9).

**Also the correct "going forward" hook point for Workday's
companies.display_name fallback** (huntloop.company_display_name.
casefold_legal_entity_name) - NOT Workday's onboarding/discovery script
(scripts/discover_and_store_workday.py). Checked the real order of
operations before wiring this: a brand-new Workday company is inserted
by that discovery script with matched_sponsor_employer_name still NULL
- this script is the one that fills that column in, and it runs later,
manually, as its own separate step (see above), never inline during
onboarding. So onboarding is the wrong place to compute this fallback;
this script - which already has the resolved name in hand for every
company it processes - is the only place a Workday company's
display_name can be derived from, both for new companies and for a
future re-run after sponsor_name_overrides changes. Scoped to
ats_platform == "workday" specifically (Lever/Ashby/iCIMS/Greenhouse/
SmartRecruiters/Gem already get a display_name from a real ATS-side
source elsewhere - this fallback is never used to override or
second-guess that), and only ever fills a currently-NULL display_name,
never overwrites one.
"""

import logging
import os
import sys
import time
from datetime import datetime, timezone

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, SRC_DIR)

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from huntloop.company_display_name import casefold_legal_entity_name  # noqa: E402
from huntloop.db_models import Company  # noqa: E402
from huntloop.logging_config import setup_logging  # noqa: E402
from huntloop.matching.fuzzy_match import find_matching_employers  # noqa: E402
from huntloop.settings import DATABASE_URL  # noqa: E402

setup_logging()
logger = logging.getLogger(__name__)


def main():
    t0 = time.perf_counter()

    engine = create_engine(DATABASE_URL, echo=False)
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        companies = session.query(Company).order_by(Company.name).all()
        logger.info(f"Resolving sponsor matches for {len(companies)} company row(s).")

        resolved = 0
        unresolved = 0
        checked_at = datetime.now(timezone.utc)
        for company in companies:
            matches = find_matching_employers(session, company.name)
            company.sponsor_checked_at = checked_at
            if matches:
                top = matches[0]
                company.matched_sponsor_employer_name = top.employer_name_normalized
                if company.ats_platform == "workday" and not company.display_name:
                    company.display_name = casefold_legal_entity_name(top.employer_name_normalized)
                resolved += 1
                logger.info(
                    f"{company.name!r} -> {top.employer_name_normalized!r} "
                    f"(score={top.score}, source={top.source})"
                )
            else:
                company.matched_sponsor_employer_name = None
                unresolved += 1
                logger.info(f"{company.name!r} -> no match above threshold, left NULL")

        session.commit()
        logger.info(
            f"Done: {resolved} resolved, {unresolved} left NULL, "
            f"{len(companies)} total, {time.perf_counter() - t0:.1f}s"
        )
    finally:
        session.close()


if __name__ == "__main__":
    main()
