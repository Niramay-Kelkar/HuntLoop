"""
Pure-logic tests for huntloop.relevance_filter.

As of 2026-09-03 (see huntloop-architecture-decisions.md entry 24 /
SESSIONS.md) is_relevant is a title-only denylist check:

    is_relevant = NOT title_matches_denylist(title)

No embedding, similarity threshold, or reference text is involved. These
tests need no torch and no database.
"""
import re

import pytest

from huntloop import relevance_filter as rf
from huntloop.relevance_filter import classify_relevance, title_matches_denylist


# --- denylist structure --------------------------------------------------

def test_denylist_has_expected_size_and_key_terms():
    # 190 hand-validated terms + "hoist operator" = 191.
    assert len(rf.DENYLIST_KEYWORDS) == 191
    assert len(set(rf.DENYLIST_KEYWORDS)) == 191  # no dupes
    for term in ("warehouse", "truck driver", "custodian", "line cook",
                 "cashier", "welder", "hoist operator", "picker/packer"):
        assert term in rf.DENYLIST_KEYWORDS


def test_classify_relevance_is_pure_negation_of_denylist():
    for title in ("Store Driver", "Staff Software Engineer", "Warehouse Associate",
                  "Account Executive", ""):
        assert classify_relevance(title) is (not title_matches_denylist(title))


def test_embedding_similarity_argument_is_ignored():
    # second arg kept only for backward-compat with huntloop.pipelines /
    # scripts/backfill_relevance.py - must not change the verdict.
    assert classify_relevance("Warehouse Associate", 0.99) is False
    assert classify_relevance("Software Engineer", 0.0) is True
    assert classify_relevance("Software Engineer") is True


# --- denylist hits: manual / blue-collar / front-line -------------------

@pytest.mark.parametrize("title", [
    "Store Driver",
    "CDL-A Truck Driver",
    "Delivery Associate",
    "Warehouse Associate",
    "Warehouse Clerk",
    "Order Picker",
    "Picker/Packer2 Labeler 1st shift",   # digit-suffix boundary fix
    "Forklift/Hoist Operator",             # "hoist operator" term (added fix)
    "CNC Operator",
    "Assembler1",                          # digit-suffix boundary fix
    "Maintenance Technician III",
    "Journeyman Electrician",
    "Automotive Technician (2nd Shift)",
    "Custodian",
    "Housekeeper",
    "Line Cook",
    "Barista",
    "Retail Sales Associate",
    "Cashier",
    "Security Guard",
    "Sanitation Worker",
    "Warehouse Selector",
    "Meat Cutter",
])
def test_denylisted_titles_are_not_relevant(title):
    assert title_matches_denylist(title) is True
    assert classify_relevance(title) is False


# --- denylist misses: word-boundary and interposed-word guards ----------

@pytest.mark.parametrize("title", [
    "Masonry Automation Engineer",     # "mason" must not match "masonry"
    "Software Engineer, Payload",       # "loader" must not match "payload"
    "Bootloader Firmware Engineer",     # "loader" must not match "bootloader"
    "Data Center Technician",           # none of the ...technician terms match
    "Site Reliability Engineer",
])
def test_non_denylisted_titles_are_relevant(title):
    assert title_matches_denylist(title) is False
    assert classify_relevance(title) is True


# --- previously HARD/SOFT-excluded business functions now pass ----------
# These were all structurally blocked by the old HARD_EXCLUDE_KEYWORDS /
# SOFT_EXCLUDE_KEYWORDS lists. The redesign is role-agnostic - only
# manual/blue-collar work is filtered - so they are relevant now.

@pytest.mark.parametrize("title", [
    "Account Executive",
    "Enterprise Account Executive",
    "Senior Sales Engineer",
    "Product Marketing Manager",
    "HR Business Partner",
    "Technical Recruiter",
    "Tax Manager",
    "Senior Accountant",
    "Corporate Counsel",
    "Chief of Staff",
    "Supply Chain Program Manager",
    "Customer Success Manager",
    "Enterprise Solutions Consultant",
    "Fraud Operations Analyst",
])
def test_previously_excluded_business_functions_now_relevant(title):
    assert classify_relevance(title) is True


# --- clinical / healthcare was never on the denylist ------------------

@pytest.mark.parametrize("title", [
    "Registered Nurse",
    "Clinical Research Coordinator",
    "Physical Therapist",
    "Medical Assistant",
    "Pharmacy Technician",
])
def test_clinical_roles_remain_relevant(title):
    assert title_matches_denylist(title) is False
    assert classify_relevance(title) is True


# --- named real rows the recompute had to get right (step 2) ----------

@pytest.mark.parametrize("title,expected", [
    ("Software Engineer", True),
    ("Forward Deployed Software Engineer", True),
    ("Sr. Forward Deployed Engineer (FDE) - Retail", True),          # old FN via "retail"
    ("Senior Solutions Architect (EDW Enterprise Data Warehouse Migrations)", True),  # old FN via "warehouse"
    ("Staff Data Warehouse Engineer", True),                          # "data warehouse" carve-out
    ("Technical Program Manager", True),
    ("Store Driver", False),
    ("Warehouse Associate", False),
    ("Custodian", False),
])
def test_named_step2_cases(title, expected):
    assert classify_relevance(title) is expected


def test_data_warehouse_carveout_does_not_leak_to_other_terms():
    # "data warehouse" only suppresses the bare "warehouse" term, not
    # every other denylist term that might co-occur.
    assert title_matches_denylist(
        "Data Warehouse Forklift Operator"
    ) is True  # still caught by "forklift operator"
    assert title_matches_denylist("Warehouse Operator, Data Warehouse team") is True


# --- retained legacy constants are still importable (back-compat) ------

def test_legacy_constants_present_but_inert():
    assert isinstance(rf.REFERENCE_TEXT, str)
    assert rf.EXCLUDE_KEYWORDS == rf.HARD_EXCLUDE_KEYWORDS + rf.SOFT_EXCLUDE_KEYWORDS
    assert callable(rf.cosine_similarity)
    # a title that only the OLD hard-exclude list would have blocked:
    assert classify_relevance("Senior Marketing Manager") is True
