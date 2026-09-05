"""
Pure-logic tests for huntloop.employment_type.normalize_employment_type().

No database, no torch. Test values are drawn from real, live-observed
raw labels across all 7 sources (Greenhouse's and Lever's messy per-
company free text, Workday's clean "Full time"/"Part time", Ashby's
"FullTime"/"Contract"/"Intern", the schema.org-style "FULL_TIME"/"OTHER"
enum iCIMS/Gem use) - see CLAUDE.md/SESSIONS.md for the full
investigation these were confirmed against.
"""
from huntloop.employment_type import (
    CONTRACT,
    FULL_TIME,
    INTERNSHIP,
    OTHER,
    PART_TIME,
    greenhouse_employment_type,
    normalize_employment_type,
)


def test_missing_or_blank_stays_none():
    # No signal at all - not the same as a recognized-but-odd value.
    assert normalize_employment_type(None) is None
    assert normalize_employment_type("") is None
    assert normalize_employment_type("   ") is None


def test_clean_enum_style_values():
    # Workday
    assert normalize_employment_type("Full time") == FULL_TIME
    assert normalize_employment_type("Part time") == PART_TIME
    # Ashby
    assert normalize_employment_type("FullTime") == FULL_TIME
    assert normalize_employment_type("PartTime") == PART_TIME
    assert normalize_employment_type("Contract") == CONTRACT
    assert normalize_employment_type("Temporary") == CONTRACT
    assert normalize_employment_type("Intern") == INTERNSHIP
    # iCIMS / Gem schema.org-style enum
    assert normalize_employment_type("FULL_TIME") == FULL_TIME
    assert normalize_employment_type("PART_TIME") == PART_TIME
    assert normalize_employment_type("CONTRACTOR") == CONTRACT
    assert normalize_employment_type("PER_DIEM") == CONTRACT
    assert normalize_employment_type("OTHER") == OTHER
    # A real, valid schema.org value with no matching bucket - Other, not
    # a guess and not NULL (the source did give a real answer).
    assert normalize_employment_type("VOLUNTEER") == OTHER


def test_messy_free_text_from_greenhouse_and_lever():
    for value in (
        "Full-time", "Full-Time", "Fulltime Employee", "Full Time Regular",
        "Full-time - NEW", "Full-Time: Experienced", "Full-Time: New Grad",
        "full-time", "Full-Time Employee", "F - Full Time", "FT",
    ):
        assert normalize_employment_type(value) == FULL_TIME, value

    for value in ("Part-time", "Part-Time"):
        assert normalize_employment_type(value) == PART_TIME, value

    for value in (
        "Contractor", "Contract", "Temporary", "Temporary Employee",
        "Fixed-term", "Fixed Term", "Independent Contractor", "Freelance",
        "Casual", "Temp",
    ):
        assert normalize_employment_type(value) == CONTRACT, value

    for value in (
        "Intern", "Summer Internship", "Working Student", "Intern/Co-op",
        "Winter Co-Op", "Coop/Intern", "I - Intern",
    ):
        assert normalize_employment_type(value) == INTERNSHIP, value

    # Real values with no confident bucket - genuinely ambiguous or not
    # in English - must land in Other, never guessed into a specific one.
    for value in ("Regular", "Employee", "Salaried Employee", "Salary",
                  "Mid-Senior Level", "Hourly", "正社員", "契約社員"):
        assert normalize_employment_type(value) == OTHER, value


def test_underscore_flsa_suffix_still_matches_the_base_word():
    # Real Greenhouse data appends an FLSA-exemption suffix with an
    # underscore ("Full-time_exempt"). A bare `\b` regex boundary would
    # not fire between "e" and "_" (both \w), silently missing this -
    # these must still resolve to their base category.
    assert normalize_employment_type("Full-time_exempt") == FULL_TIME
    assert normalize_employment_type("Full-time_non-exempt") == FULL_TIME
    assert normalize_employment_type("Part-time_non-exempt") == PART_TIME
    assert normalize_employment_type("Internship_non-exempt") == INTERNSHIP


def test_mixed_signal_prefers_the_more_specific_category():
    # Contract/temporary/intern status is more decisive than the hours
    # commitment mentioned alongside it.
    assert normalize_employment_type("Temporary Full-Time") == CONTRACT
    assert normalize_employment_type("Full Time - Fixed Term Employment") == CONTRACT
    assert normalize_employment_type("Intern - Fixed Term - Trainee") == INTERNSHIP


def test_international_is_not_misread_as_an_internship():
    # "intern" is a substring of "international" - a naive substring
    # check would wrongly bucket this as an internship.
    assert normalize_employment_type("Vice President of International Sales") == OTHER
    assert normalize_employment_type("International") == OTHER


# ---------------------------------------------------------------------------
# greenhouse_employment_type(): picking the best label out of a company's
# free-form `metadata` array (literal "Employment Type" first, then a
# curated set of adjacent fields). Field names/values are real, live-
# observed - see CLAUDE.md/SESSIONS.md.
# ---------------------------------------------------------------------------


def _md(*pairs):
    return [{"name": n, "value": v} for n, v in pairs]


def test_greenhouse_literal_employment_type_wins_and_keeps_other():
    # The explicitly-named field is authoritative - an unrecognized value
    # there is a real answer and legitimately normalizes to Other.
    assert greenhouse_employment_type(_md(("Employment Type", "Full-time"))) == "Full-time"
    assert (
        normalize_employment_type(
            greenhouse_employment_type(_md(("Employment Type", "Regular")))
        )
        == OTHER
    )


def test_greenhouse_falls_back_to_time_type_when_no_employment_type():
    assert greenhouse_employment_type(_md(("Time Type", "Full time"))) == FULL_TIME
    assert greenhouse_employment_type(_md(("Time Type", "Part-time"))) == PART_TIME
    assert (
        greenhouse_employment_type(_md(("Full-time/ Part-time", "Full-time"))) == FULL_TIME
    )
    assert (
        greenhouse_employment_type(_md(("Employment Status", "Regular Full-time")))
        == FULL_TIME
    )
    assert greenhouse_employment_type(_md(("Work Type", "Intern, Full-time"))) == INTERNSHIP
    assert greenhouse_employment_type(_md(("WORKER_CATEGORY", "Contract"))) == CONTRACT


def test_greenhouse_literal_field_takes_priority_over_adjacent_fields():
    entries = _md(("Time Type", "Part-time"), ("Employment Type", "Full-time"))
    assert greenhouse_employment_type(entries) == "Full-time"


def test_greenhouse_adjacent_field_ignored_when_value_is_not_a_specific_bucket():
    # "peo" under Employment Status is real but maps to Other - for a
    # secondary field that means "not actually an employment type here",
    # so it must be ignored (leave NULL), not stored as Other.
    assert greenhouse_employment_type(_md(("Employment Status", "peo"))) is None


def test_greenhouse_rejected_fields_are_not_consulted():
    # Worker Type / Pay Rate Type / Employee Type / Job Type were
    # investigated and deliberately excluded - their real dominant values
    # ("Employee", "Salary", "Regular", "Standard") don't describe
    # employment type. A "(PT)" in a Job Type role name must NOT leak
    # through as Part-time.
    assert greenhouse_employment_type(_md(("Worker Type", "Employee"))) is None
    assert greenhouse_employment_type(_md(("Pay Rate Type", "Salary"))) is None
    assert greenhouse_employment_type(_md(("Employee Type", "Regular"))) is None
    assert greenhouse_employment_type(_md(("Job Type", "Physical Therapist (PT)"))) is None


def test_greenhouse_handles_empty_null_and_malformed_metadata():
    assert greenhouse_employment_type(None) is None
    assert greenhouse_employment_type([]) is None
    assert greenhouse_employment_type("not a list") is None
    assert greenhouse_employment_type([{"name": "Time Type", "value": None}]) is None
    assert greenhouse_employment_type([{"name": "Time Type"}, "junk", 42]) is None
