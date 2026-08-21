import pytest

from huntloop.db_models import LcaDisclosure, SponsorNameOverride
from huntloop.matching.fuzzy_match import find_matching_employers


def _disclosure(case_number, employer_name_normalized):
    """A minimally-valid LcaDisclosure row for seeding matching tests -
    only employer_name_normalized matters to find_matching_employers."""
    return LcaDisclosure(
        case_number=case_number,
        employer_name=employer_name_normalized,
        employer_name_normalized=employer_name_normalized,
        case_status="Certified",
        job_title="Engineer",
        fiscal_year=2025,
        quarter=1,
        source_file="test.xlsx",
    )


@pytest.fixture()
def seeded_session(db_session):
    # Real raw EMPLOYER_NAME variants found in the ingested LCA data,
    # already run through normalize_employer_name.
    db_session.add_all(
        [
            _disclosure("AMZ-1", "AMAZON"),
            _disclosure("AMZ-2", "AMAZON WEB SERVICES"),
            _disclosure("AMZ-3", "AMAZON DEVELOPMENT CENTER US"),
            _disclosure("GOOG-1", "GOOGLE"),
            _disclosure("GOOG-2", "GOOGLE PUBLIC SECTOR"),
            _disclosure("INFY-1", "INFOSYS LIMITED"),
            _disclosure("AA-1", "A&A INFOSYSTEMS"),
            _disclosure("OTHER-1", "UNRELATED WIDGET MAKERS"),
        ]
    )
    db_session.commit()
    return db_session


def test_amazon_variants_rank_above_unrelated_companies(seeded_session):
    results = find_matching_employers(seeded_session, "Amazon")
    matched_names = [r.employer_name_normalized for r in results]

    assert "AMAZON" in matched_names
    assert "AMAZON WEB SERVICES" in matched_names
    assert "AMAZON DEVELOPMENT CENTER US" in matched_names
    assert "GOOGLE" not in matched_names
    assert "UNRELATED WIDGET MAKERS" not in matched_names

    # scores are sorted descending, and every Amazon variant outranks every
    # returned non-Amazon result (there shouldn't be any here, but this
    # also guards against a future looser threshold letting one through).
    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True)
    amazon_scores = [r.score for r in results if r.employer_name_normalized.startswith("AMAZON")]
    other_scores = [r.score for r in results if not r.employer_name_normalized.startswith("AMAZON")]
    assert all(a >= o for a in amazon_scores for o in other_scores)


def test_google_variants_rank_above_unrelated_companies(seeded_session):
    results = find_matching_employers(seeded_session, "Google")
    matched_names = [r.employer_name_normalized for r in results]

    assert "GOOGLE" in matched_names
    assert "GOOGLE PUBLIC SECTOR" in matched_names
    assert "AMAZON" not in matched_names
    assert "UNRELATED WIDGET MAKERS" not in matched_names


def test_infosys_query_does_not_match_a_and_a_infosystems(seeded_session):
    # The Step 1 audit's flagged false-positive risk: "INFOSYS" and
    # "A&A INFOSYSTEMS" share a lot of characters but are different
    # companies, and must not be treated as a match at the default
    # threshold.
    results = find_matching_employers(seeded_session, "Infosys")
    matched_names = [r.employer_name_normalized for r in results]

    assert "INFOSYS LIMITED" in matched_names
    assert "A&A INFOSYSTEMS" not in matched_names


def test_override_short_circuits_fuzzy_matching(seeded_session):
    seeded_session.add(
        SponsorNameOverride(
            raw_company_name="Weird Co Formerly Known As Something Else",
            employer_name_normalized="AMAZON WEB SERVICES",
        )
    )
    seeded_session.commit()

    results = find_matching_employers(seeded_session, "Weird Co Formerly Known As Something Else")

    assert len(results) == 1
    assert results[0].employer_name_normalized == "AMAZON WEB SERVICES"
    assert results[0].score == 100.0
    assert results[0].source == "override"


def test_override_lookup_is_case_insensitive(seeded_session):
    seeded_session.add(
        SponsorNameOverride(raw_company_name="Some Company", employer_name_normalized="AMAZON")
    )
    seeded_session.commit()

    results = find_matching_employers(seeded_session, "SOME COMPANY")

    assert len(results) == 1
    assert results[0].source == "override"


def test_no_override_falls_back_to_fuzzy_matching(seeded_session):
    results = find_matching_employers(seeded_session, "Amazon")
    assert all(r.source == "fuzzy" for r in results)


def test_unrelated_query_returns_no_matches(seeded_session):
    results = find_matching_employers(seeded_session, "Totally Unrelated Business Name")
    assert results == []
