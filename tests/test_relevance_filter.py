"""
Pure-logic tests for huntloop.relevance_filter.classify_relevance -
especially the 2026-08-30 hard/soft exclude split (see SESSIONS.md).

classify_relevance(title, embedding_similarity) is pure: the caller
supplies the already-computed category similarity, so these need no
torch. The real similarity values used in the "named example" tests
below come from scripts/calibrate_soft_exclude_threshold.py run against
the real dataset (see SESSIONS.md 2026-08-30 for the full table).
"""
import pytest

from huntloop import relevance_filter as rf
from huntloop.relevance_filter import classify_relevance

HI = 0.99   # clearly-technical similarity
LO = 0.05   # clearly-non-technical similarity
RESCUE = rf.SOFT_EXCLUDE_RESCUE_THRESHOLD
BASE = rf.EMBEDDING_SIMILARITY_THRESHOLD


# --- structure of the split ------------------------------------------------

def test_exclude_keywords_is_hard_plus_soft():
    assert rf.EXCLUDE_KEYWORDS == rf.HARD_EXCLUDE_KEYWORDS + rf.SOFT_EXCLUDE_KEYWORDS
    assert set(rf.SOFT_EXCLUDE_KEYWORDS) == {"customer success", "solutions consultant"}
    # the two soft phrases must NOT also be in the hard list
    assert "customer success" not in rf.HARD_EXCLUDE_KEYWORDS
    assert "solutions consultant" not in rf.HARD_EXCLUDE_KEYWORDS


def test_rescue_threshold_is_stricter_than_base_threshold():
    assert RESCUE >= BASE


@pytest.mark.parametrize("kw", ["sales", "marketing", "legal", "tax", "recruiter",
                                "manufacturing", "warehouse", "retail", "chief of staff",
                                "human resources", "supply chain", "customer support"])
def test_hard_excludes_unchanged_and_absolute(kw):
    title = f"Staff {kw.title()} Engineer"
    assert rf.keyword_hard_exclude_match(title) is True
    # absolute: excluded even with a maximal similarity
    assert classify_relevance(title, HI) is False


# --- hard exclude wins over everything -----------------------------------

def test_sales_engineer_still_hard_excluded():
    assert classify_relevance("Senior Sales Engineer", HI) is False


def test_hard_beats_soft_in_same_title():
    # "Sales Solutions Consultant" - hard "sales" must win regardless of similarity
    assert classify_relevance("Sales Solutions Consultant", HI) is False


# --- soft exclude: embedding-gated --------------------------------------

def test_soft_exclude_below_threshold_still_excluded():
    assert classify_relevance("Customer Success Manager", LO) is False
    assert classify_relevance("Solutions Consultant, Federal", LO) is False


def test_soft_exclude_at_or_above_threshold_is_rescued():
    assert classify_relevance("Enterprise Solutions Consultant", RESCUE) is True
    assert classify_relevance("Enterprise Solutions Consultant", RESCUE + 0.01) is True


def test_soft_exclude_engineer_title_does_not_auto_rescue():
    # "Customer Success Engineer" contains the include keyword "engineer"
    # but must stay excluded when the semantic signal is weak.
    assert rf.keyword_include_match("Senior Customer Success Engineer") is True
    assert classify_relevance("Senior Customer Success Engineer", LO) is False
    # ...and IS rescued if the semantic signal is strong
    assert classify_relevance("Senior Customer Success Engineer", HI) is True


def test_soft_exclude_just_below_threshold_excluded():
    assert classify_relevance("Enterprise Solutions Consultant", RESCUE - 0.001) is False


# --- unrelated titles unaffected --------------------------------------

def test_plain_technical_title_relevant():
    assert classify_relevance("iOS Engineer", LO) is True          # include keyword
    assert classify_relevance("Site Reliability", LO) is True      # include phrase


def test_plain_nontechnical_title_not_relevant():
    assert classify_relevance("Fraud Operations Specialist", LO) is False
    assert classify_relevance("Office Coordinator", LO) is False


# --- named real examples this change must get right -------------------
# Real measured category similarities from
# scripts/calibrate_soft_exclude_threshold.py run against the real
# dataset (2026-08-30, see SESSIONS.md). Pinned here so a future
# threshold change can't silently break the three cases the task called
# out.
NAMED_EXAMPLES = [
    # (title, real category similarity, expected is_relevant after the fix)
    ("Forward Deployed Enablement Engineer - Customer Success", 0.4394, True),
    ("Enterprise Solutions Consultant (Bengaluru, India)", 0.3403, True),
    ("Senior Customer Success Engineer", 0.3232, False),
]


@pytest.mark.parametrize("title,sim,expected", NAMED_EXAMPLES)
def test_named_real_examples(title, sim, expected):
    assert classify_relevance(title, sim) is expected


def test_named_examples_bracket_the_threshold():
    # Rubrik (excluded) must be below and Figma (rescued) at/above.
    assert 0.3232 < RESCUE <= 0.3403
