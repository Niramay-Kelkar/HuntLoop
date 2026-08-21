"""
Sponsorship lookup for a scraped `companies` row, built on top of Step 6's
find_matching_employers().

get_sponsorship_summary() takes a Company and answers "does this company
sponsor, and how much" by finding its likely employer_name_normalized
matches in lca_disclosures and aggregating across them: total approved
LCAs per fiscal year, distinct job titles sponsored, distinct worksite
states.

Deliberately NOT a stored/FK relationship - a Company can span multiple
legal entities in the LCA data (e.g. a parent and subsidiaries), and match
confidence varies row to row, so this stays a queryable lookup computed at
call time rather than a rigid one-to-one link baked into the schema.
"""

from collections import Counter
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from huntloop.db_models import Company, LcaDisclosure
from huntloop.matching.fuzzy_match import (
    DEFAULT_LIMIT,
    DEFAULT_THRESHOLD,
    EmployerMatch,
    find_matching_employers,
)


@dataclass(frozen=True)
class SponsorshipSummary:
    company_name: str
    matched_employer_names: list[EmployerMatch]
    total_approved_lcas: int
    approved_lcas_by_fiscal_year: dict[int, int]
    distinct_job_titles: int
    distinct_worksite_states: int
    job_titles: list[str]
    worksite_states: list[str]


def get_sponsorship_summary(
    session: Session,
    company: Company,
    threshold: int = DEFAULT_THRESHOLD,
    limit: int = DEFAULT_LIMIT,
) -> SponsorshipSummary:
    """Aggregate lca_disclosures across every employer_name_normalized
    value find_matching_employers() finds for company.name."""
    matches = find_matching_employers(session, company.name, threshold=threshold, limit=limit)
    matched_names = [m.employer_name_normalized for m in matches]

    if not matched_names:
        return SponsorshipSummary(
            company_name=company.name,
            matched_employer_names=[],
            total_approved_lcas=0,
            approved_lcas_by_fiscal_year={},
            distinct_job_titles=0,
            distinct_worksite_states=0,
            job_titles=[],
            worksite_states=[],
        )

    rows = session.execute(
        select(
            LcaDisclosure.fiscal_year,
            LcaDisclosure.job_title,
            LcaDisclosure.worksite_state,
        ).where(LcaDisclosure.employer_name_normalized.in_(matched_names))
    ).all()

    by_fiscal_year = Counter(row.fiscal_year for row in rows)
    job_titles = sorted({row.job_title for row in rows if row.job_title})
    worksite_states = sorted({row.worksite_state for row in rows if row.worksite_state})

    return SponsorshipSummary(
        company_name=company.name,
        matched_employer_names=matches,
        total_approved_lcas=len(rows),
        approved_lcas_by_fiscal_year=dict(sorted(by_fiscal_year.items())),
        distinct_job_titles=len(job_titles),
        distinct_worksite_states=len(worksite_states),
        job_titles=job_titles,
        worksite_states=worksite_states,
    )
