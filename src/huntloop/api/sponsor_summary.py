"""
API-facing sponsor summary, built on Step 8's persisted
`companies.matched_sponsor_employer_name` - deliberately NOT the same as
`huntloop.matching.sponsorship.get_sponsorship_summary()` (which still
does live fuzzy-matching across every candidate employer name, for the
broader multi-fiscal-year/multi-title aggregate use case it was built
for). This module's `get_sponsorship_summary()` is scoped to what
`GET /jobs/{id}` actually needs, reads the single persisted match instead
of re-running `find_matching_employers()` per request (per this step's
explicit "no live fuzzy-matching per request" requirement), and returns
`None` outright when a company has no resolved match - callers don't need
to inspect an empty result to tell "no sponsor data" from "sponsor found".

Median wage is computed over WAGE_UNIT_OF_PAY = 'Year' rows only. Checked
against real data before deciding this (see SESSIONS.md): of 3,527 LCA
rows across the 9 real matched companies, all but 5 are labeled 'Year'.
Of those 5, two (Duolingo's "Marketing Analytics Manager, Growth" at
$135,000/'Week' and "Senior Software Engineer, Platform" at
$220,000/'Month') are unmistakably annual salaries mislabeled with the
wrong unit - the same Phase 1-audit data-entry error pattern, confirmed
again here on this exact table. A third (Adobe's "Solutions Consulting
Analyst" at $38.87/'Hour') is a plausible genuine hourly rate. Rather
than trying to annualize each unit (and risk compounding a mislabeled
row further), this excludes all non-'Year' rows outright - they're under
0.15% of the matched-company rows, so excluding them costs negligible
sample size while fully removing the contamination risk.
"""

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from huntloop.db_models import Company, LcaDisclosure

# The mislabeled-unit pattern the Phase 1 audit originally found, and
# reconfirmed against this table's real data in this module's docstring
# above - only rows filed in whole-year salary terms are trustworthy for
# a median.
_ANNUAL_WAGE_UNIT = "Year"

SALARY_ESTIMATE_BASIS = "Estimated from DOL wage filings for this employer, not job-specific"


@dataclass(frozen=True)
class SponsorSummary:
    matched_employer_name: str
    most_recent_fiscal_year: int
    total_lcas_most_recent_fiscal_year: int
    median_wage: float | None
    most_frequent_job_title: str | None
    latest_case_status: str | None


def get_sponsorship_summary(session: Session, company: Company) -> SponsorSummary | None:
    """Sponsor summary for `company`, from its persisted
    `matched_sponsor_employer_name` (Step 8's scripts/resolve_sponsor_matches.py) -
    no live matching. Returns None if the company has no resolved match, or
    if that matched name turns out to have no lca_disclosures rows (e.g.
    the match was resolved before some data change - defensive, not
    expected in practice)."""
    name = company.matched_sponsor_employer_name
    if not name:
        return None

    most_recent_fy = session.execute(
        select(func.max(LcaDisclosure.fiscal_year)).where(LcaDisclosure.employer_name_normalized == name)
    ).scalar_one_or_none()
    if most_recent_fy is None:
        return None

    total_recent_fy = session.execute(
        select(func.count())
        .select_from(LcaDisclosure)
        .where(
            LcaDisclosure.employer_name_normalized == name,
            LcaDisclosure.fiscal_year == most_recent_fy,
        )
    ).scalar_one()

    median_wage = session.execute(
        select(func.percentile_cont(0.5).within_group(LcaDisclosure.wage_rate_of_pay_from)).where(
            LcaDisclosure.employer_name_normalized == name,
            LcaDisclosure.wage_unit_of_pay == _ANNUAL_WAGE_UNIT,
        )
    ).scalar_one_or_none()

    top_title_row = session.execute(
        select(LcaDisclosure.job_title, func.count().label("filing_count"))
        .where(LcaDisclosure.employer_name_normalized == name)
        .group_by(LcaDisclosure.job_title)
        .order_by(func.count().desc())
        .limit(1)
    ).first()
    most_frequent_job_title = top_title_row.job_title if top_title_row is not None else None

    latest_row = session.execute(
        select(LcaDisclosure.case_status)
        .where(LcaDisclosure.employer_name_normalized == name)
        .order_by(LcaDisclosure.received_date.desc())
        .limit(1)
    ).first()
    latest_case_status = latest_row.case_status if latest_row is not None else None

    return SponsorSummary(
        matched_employer_name=name,
        most_recent_fiscal_year=most_recent_fy,
        total_lcas_most_recent_fiscal_year=total_recent_fy,
        median_wage=float(median_wage) if median_wage is not None else None,
        most_frequent_job_title=most_frequent_job_title,
        latest_case_status=latest_case_status,
    )
