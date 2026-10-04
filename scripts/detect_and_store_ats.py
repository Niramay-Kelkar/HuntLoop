"""
One-off script: run detect_ats() (huntloop.ats_detection) against a small,
manually-curated list of real company careers URLs, and upsert the result
into the companies table's ats_platform/ats_token/careers_url columns.

Not part of the app's ongoing pipeline - run manually via:

    python scripts/detect_and_store_ats.py

This step is detect-then-store only - it does not run any spider or
scrape any jobs, and does not wire the result into main.py or any
multi-spider dispatch logic. That's a separate later step, once this data
exists to route on.

Company names use the same lowercase-token convention the real spiders
use for `company_name` (e.g. "checkr", not "Checkr") - matching that
convention here means this script upserts the *same* companies row a
spider would use, rather than creating a second, differently-cased row
for the same company (the exact class of bug just fixed for job_sources -
see SESSIONS.md 2026-08-21 - so it's deliberately not repeated here for
companies).

The curated list below mixes companies already confirmed
Greenhouse/Lever/Ashby/Workday from the ATS-detection testing sessions
(SESSIONS.md, 2026-08-21) with a couple of companies detect_ats() has
never seen before (brex, figma), to check whether it holds up outside the
set it was tuned against.

upsert_company_ats() deliberately does not overwrite an existing,
previously-successful ats_platform/ats_token value when the current
detection attempt errored (detect_ats()'s `error` field set, e.g. a
transient network timeout) - only a genuine "checked successfully, no
pattern matched" result overwrites with "unknown". See SESSIONS.md
(2026-08-21) for the real transient ReadTimeout this fixes: a first run
correctly stored checkr as greenhouse, a second run's fetch timed out and
used to silently blank that back to unknown/NULL even though nothing
about checkr's real ATS had changed.
"""
import logging

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from huntloop.ats_detection import detect_ats
from huntloop.company_display_name import fetch_greenhouse_display_name
from huntloop.db_models import Company

load_dotenv()

from huntloop.settings import DATABASE_URL

logger = logging.getLogger(__name__)

# (company_name, careers_url) - company_name follows the same lowercase
# convention the real spiders use for JobPostingItem.company_name.
COMPANIES = [
    # Previously confirmed Greenhouse (SESSIONS.md, ATS-detection session)
    ("checkr", "https://job-boards.greenhouse.io/checkr"),
    ("duolingo", "https://job-boards.greenhouse.io/duolingo"),
    # Previously confirmed Lever
    ("kraken", "https://jobs.lever.co/kraken"),
    ("palantir", "https://jobs.lever.co/palantir"),
    ("wealthfront", "https://jobs.lever.co/wealthfront"),
    # Previously confirmed Ashby
    ("ramp", "https://jobs.ashbyhq.com/ramp"),
    # Previously confirmed Workday
    ("adobe", "https://adobe.wd5.myworkdayjobs.com/en-US/external_experienced"),
    # New - not tested against detect_ats() before this script
    ("brex", "https://brex.com/careers"),
    ("figma", "https://www.figma.com/careers/"),
]


def upsert_company_ats(session, company_name: str, careers_url: str, result: dict) -> None:
    company = session.query(Company).filter_by(name=company_name).first()
    if not company:
        company = Company(name=company_name)
        session.add(company)
        logger.info(f"Creating new company row: {company_name}")
    else:
        logger.info(f"Updating existing company row: {company_name}")

    company.careers_url = careers_url

    # A fetch/network error means this attempt couldn't actually check the
    # page - distinct from a genuine "checked successfully, no pattern
    # matched" result (which is a real, current "unknown" and should still
    # overwrite). Only skip the overwrite when there's an existing,
    # previously-successful value to protect; if this company has never
    # been successfully detected before (ats_platform is still NULL),
    # there's nothing to protect, so storing unknown/NULL is fine.
    if result["error"] is not None and company.ats_platform is not None:
        logger.warning(
            f"{company_name}: detection attempt failed ({result['error']}) - "
            f"leaving existing stored value unchanged "
            f"(ats_platform={company.ats_platform!r}, ats_token={company.ats_token!r})"
        )
    else:
        company.ats_platform = result["ats"]
        company.ats_token = result["identifier"]
        if result["ats"] == "greenhouse" and not company.display_name:
            company.display_name = fetch_greenhouse_display_name(result["identifier"])

    session.commit()


def main():
    engine = create_engine(DATABASE_URL, echo=False)
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        for company_name, careers_url in COMPANIES:
            result = detect_ats(careers_url)
            logger.info(
                f"{company_name}: ats={result['ats']} identifier={result['identifier']} "
                f"render_attempted={result['render_attempted']} error={result['error']}"
            )
            upsert_company_ats(session, company_name, careers_url, result)
    finally:
        session.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
