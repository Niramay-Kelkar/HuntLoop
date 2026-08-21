"""
Fuzzy company-name matching on top of Step 5's mechanical normalization
(huntloop.matching.normalize.normalize_employer_name).

find_matching_employers() takes a company name (e.g. from the scraped
`companies` table) and returns candidate `employer_name_normalized` values
from `lca_disclosures`, ranked by similarity score. It first checks
`sponsor_name_overrides` for a manually-confirmed mapping and short-circuits
to it if one exists, without running fuzzy matching at all.

Not wired up to `companies` yet, and `sponsor_name_overrides` isn't
populated with real curated entries yet - this module is just the matching
mechanism (Step 6). Applying it to real scraped companies and curating
overrides are later steps.
"""

from dataclasses import dataclass

from rapidfuzz import fuzz, process
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from huntloop.db_models import LcaDisclosure, SponsorNameOverride
from huntloop.matching.normalize import normalize_employer_name

# rapidfuzz's WRatio was tried first and rejected: it includes a
# partial-ratio component that activates whenever one string is much
# shorter than the other, and that alone scores "INFOSYS" vs.
# "A&A INFOSYSTEMS" at 90 - the exact false positive the Step 1 audit
# flagged, indistinguishable from a true match. token_set_ratio instead
# compares tokens: it still tolerates reordering and extra legal-entity
# words (so "Amazon" matches "AMAZON WEB SERVICES" and "AMAZON DEVELOPMENT
# CENTER US" at 100), but "INFOSYS" and "INFOSYSTEMS" are different tokens,
# so "INFOSYS" vs. "A&A INFOSYSTEMS" scores only 63.6 under it - a wide,
# reliable margin below any sane threshold. See SESSIONS.md for the full
# score comparison this decision was based on.
_SCORER = fuzz.token_set_ratio

# Empirically: true variants of the same company consistently score 100
# once normalized (Amazon's and Google's subsidiary/division names all do).
# The Step 1 audit's flagged false positive ("INFOSYS" vs. "A&A
# INFOSYSTEMS") scores 63.6. The tightest real near-miss found while tuning
# this - two unrelated small companies that happen to share a generic
# industry-suffix word, e.g. "Nous Infosystems" vs. "VNS Infosystems"
# (only "INFOSYSTEMS" in common) - tops out around 90. 88 sits in the gap
# between that near-miss cluster and the 100-scoring true positives: comfortably
# above the audit's flagged case (63.6, ~24 points of margin) while still
# excluding most coincidental generic-suffix overlaps. It will not catch
# every such overlap (some near-misses score in the high 80s/low 90s) -
# that residual ambiguity is exactly what sponsor_name_overrides is for,
# not something a single global threshold should be tuned to eliminate
# entirely. See SESSIONS.md for the concrete scores this was chosen
# against.
DEFAULT_THRESHOLD = 88

DEFAULT_LIMIT = 10


@dataclass(frozen=True)
class EmployerMatch:
    employer_name_normalized: str
    score: float
    source: str  # "override" or "fuzzy"


def find_matching_employers(
    session: Session,
    query_name: str,
    threshold: int = DEFAULT_THRESHOLD,
    limit: int = DEFAULT_LIMIT,
) -> list[EmployerMatch]:
    """Return candidate employer_name_normalized values for query_name,
    ranked by score (highest first).

    Checks sponsor_name_overrides for an exact (case-insensitive) match on
    raw_company_name first; if found, returns that single confirmed mapping
    and does not run fuzzy matching at all. Otherwise, normalizes
    query_name the same way LCA employer names are normalized and fuzzy-
    matches it against the distinct employer_name_normalized values in
    lca_disclosures, keeping only matches scoring at or above `threshold`.
    """
    override = session.execute(
        select(SponsorNameOverride).where(
            func.lower(SponsorNameOverride.raw_company_name) == query_name.strip().lower()
        )
    ).scalar_one_or_none()
    if override is not None:
        return [
            EmployerMatch(
                employer_name_normalized=override.employer_name_normalized,
                score=100.0,
                source="override",
            )
        ]

    normalized_query = normalize_employer_name(query_name)
    candidates = [
        value
        for value in session.execute(
            select(LcaDisclosure.employer_name_normalized).distinct()
        ).scalars()
        if value
    ]
    if not candidates:
        return []

    results = process.extract(
        normalized_query,
        candidates,
        scorer=_SCORER,
        limit=limit,
        score_cutoff=threshold,
    )
    return [
        EmployerMatch(employer_name_normalized=match, score=score, source="fuzzy")
        for match, score, _index in results
    ]
