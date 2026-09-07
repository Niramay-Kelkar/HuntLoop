"""
Pure-logic tests for huntloop.department_categorization.rule_based_category().

No database, no network. The raw values below are real, live-observed
`job_postings.department` strings sampled across the whole frequency
spectrum (see CLAUDE.md/SESSIONS.md for the investigation) - the clean
head, the company-specific tail, and genuinely unclassifiable values.
"""
import pytest

from huntloop.department_categorization import (
    CANONICAL_CATEGORIES,
    OTHER,
    categorize_values,
    rule_based_category,
)

# (raw value, expected canonical category) - all from real data.
CLEAN_HEAD = [
    ("Engineering", "Engineering"),
    ("Software Engineering", "Engineering"),
    ("Infrastructure", "Engineering"),
    ("Hardware Engineering", "Engineering"),
    ("Sales", "Sales"),
    ("Enterprise Sales", "Sales"),
    ("Marketing", "Marketing"),
    ("Product", "Product"),
    ("Product Management", "Product"),
    ("Design", "Design"),
    ("Finance", "Finance & Accounting"),
    ("Legal", "Legal & Compliance"),
    ("IT", "IT"),
    ("Information Technology", "IT"),
    ("People", "People & HR"),
    ("Human Resources", "People & HR"),
    ("Operations", "Operations"),
    ("Logistics", "Operations"),
    ("Customer Success", "Customer Support"),
    ("Customer Experience", "Customer Support"),
    ("Data Science", "Data & Analytics"),
    ("Research", "Research & Science"),
    ("Testing & Laboratory", "Research & Science"),
    ("Production & Manufacturing", "Manufacturing & Production"),
    ("Consulting", "Consulting & Professional Services"),
    ("Professional Services", "Consulting & Professional Services"),
    ("RN", "Healthcare & Clinical"),
    ("Clinical Tech", "Healthcare & Clinical"),
    ("Direct Care", "Healthcare & Clinical"),
]

MESSY_TAIL = [
    ("R&D - Backend Infra", "Engineering"),
    ("SW Eng - Core Identity-670", "Engineering"),
    ("Voice & Infrastructure - Product Engineering", "Engineering"),
    ("20213 S&M - Sales - Square Outside", "Sales"),
    ("Field Engineering - Other", "Construction & Skilled Trades"),
    ("Data Engineering", "Data & Analytics"),
    ("Administrative/Clerical", "Operations"),
    ("Post Production", "Manufacturing & Production"),
    ("Reconditioning", "Manufacturing & Production"),
    ("LVN/LPN", "Healthcare & Clinical"),
    ("Faculty/Provider", "Healthcare & Clinical"),
    ("Steuerberatung", "Finance & Accounting"),  # German: tax advisory
    ("Go To Market", "Sales"),
    ("Account Management", "Sales"),
    ("Technical Program Management", "Product"),
    ("Real estate", "Construction & Skilled Trades"),
    ("Energy and natural resources", "Construction & Skilled Trades"),
    ("Inspection", "Construction & Skilled Trades"),
    ("General Management", "Executive & General Management"),
]

# Disambiguation: a broader word appears but a more specific rule wins.
DISAMBIGUATION = [
    ("Sales Engineer", "Sales"),
    ("Solutions Consultant", "Sales"),
    ("Sales Operations", "Operations"),
    ("People Operations", "People & HR"),
    ("Clinical Research", "Research & Science"),
]

# Genuinely unclassifiable - rules must NOT guess (return None).
UNCLASSIFIABLE = [
    "Woven City",
    "zz-Evergreen Requisition",
    "Ω ARCHIVE - Do not remove",
    "CPXO",
    "1Z_TWIND SL DAMO",
]


@pytest.mark.parametrize("raw,expected", CLEAN_HEAD + MESSY_TAIL + DISAMBIGUATION)
def test_rule_based_category_maps_real_values(raw, expected):
    assert rule_based_category(raw) == expected


@pytest.mark.parametrize("raw", UNCLASSIFIABLE)
def test_rule_based_category_returns_none_for_unclassifiable(raw):
    assert rule_based_category(raw) is None


def test_blank_and_none_return_none():
    assert rule_based_category(None) is None
    assert rule_based_category("") is None
    assert rule_based_category("   ") is None


def test_every_rule_output_is_a_canonical_category():
    for raw, _ in CLEAN_HEAD + MESSY_TAIL + DISAMBIGUATION:
        assert rule_based_category(raw) in CANONICAL_CATEGORIES


def test_categorize_values_without_llm_sends_residual_to_other():
    values = ["Engineering", "Woven City", None, "  ", "Sales"]
    mapping = categorize_values(values, use_llm=False)
    assert mapping["Engineering"] == "Engineering"
    assert mapping["Sales"] == "Sales"
    assert mapping["Woven City"] == OTHER
    # Blank/None are dropped, not carried through as keys.
    assert set(mapping) == {"Engineering", "Sales", "Woven City"}
