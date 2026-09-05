"""Normalize each source's raw employment-type label into a small,
consistent set: "Full-time", "Part-time", "Contract", "Internship", or
"Other".

Every one of the 7 spiders already extracts SOME raw employment-type
signal into ``item["employment_type"]`` where its source genuinely
exposes one (Greenhouse: a free-form "Employment Type"/"LEGACY -
Employment Type" custom-field entry in its per-company `metadata` array;
Lever: `categories.commitment`; Workday: `jobPostingInfo.timeType`;
SmartRecruiters: `typeOfEmployment.label`; Ashby: `employmentType`;
iCIMS: the JSON-LD `employmentType` enum; Gem: `job.employmentType`) -
this module is the one place that turns those wildly different raw
shapes into a single consistent value the API/frontend filter can use.

Confirmed against real live data before writing this (see
CLAUDE.md/SESSIONS.md for the full per-source investigation), not
assumed:
  - Workday and Ashby/Gem/iCIMS are clean, enum-like values
    ("Full time"/"Part time", "FullTime"/"Contract"/"Intern",
    "FULL_TIME", "FULL_TIME"/"OTHER" respectively).
  - Greenhouse and Lever are genuinely messy, per-company free text
    (real observed values include "Regular", "Fulltime Employee",
    "Full-Time: Experienced", "正社員", "Modified Full-Time", ...) - most
    of it maps cleanly, but some of it (e.g. bare "Regular", "Employee",
    "Salary") is real data with no confident bucket.

"Other" means the source gave a real, non-empty label that doesn't map
to one of the four specific buckets - it is NOT a guess and is not the
same as NULL. NULL is reserved for "this source/posting genuinely
provides no signal at all" (a source with no such field at all, or an
empty string). Never invent Full-time/Part-time/Contract/Internship for
an ambiguous raw label just because it's the most common case - that
would misrepresent data the source never actually gave us.
"""
from __future__ import annotations

import re

FULL_TIME = "Full-time"
PART_TIME = "Part-time"
CONTRACT = "Contract"
INTERNSHIP = "Internship"
OTHER = "Other"

# Order matters: checked most-specific-first so a label naming more than
# one signal (e.g. real data has "Temporary Full-Time") is bucketed by
# the more decisive one - knowing a role is temporary/an internship is a
# stronger, more specific signal than the hours commitment alongside it.
#
# Trailing boundaries use a negative lookahead for a following letter
# rather than a bare `\b` - real Greenhouse data appends an FLSA-exempt
# suffix with an underscore ("Full-time_exempt", "Internship_non-exempt"),
# and `\b` doesn't fire between two word characters (`e` and `_` are both
# `\w`), which would otherwise silently miss those real, observed values.
_NOT_FOLLOWED_BY_LETTER = r"(?![a-zA-Z])"
_INTERNSHIP_RE = re.compile(
    rf"\bintern(ship)?{_NOT_FOLLOWED_BY_LETTER}|\bco[\s\-]?op\b|\btrainee\b|"
    rf"\bapprentice\b|\bworking\s+student\b",
    re.IGNORECASE,
)
_CONTRACT_RE = re.compile(
    rf"\bcontract\w*{_NOT_FOLLOWED_BY_LETTER}|\btemp(orary)?{_NOT_FOLLOWED_BY_LETTER}|"
    rf"\bfixed[\s\-]?term\b|\bfreelance\b|\bcasual\b|\bper[\s\-_]?diem\b|\bseasonal\b",
    re.IGNORECASE,
)
_FULL_TIME_RE = re.compile(
    rf"\bfull[\s\-_]?time{_NOT_FOLLOWED_BY_LETTER}|\bft\b", re.IGNORECASE
)
_PART_TIME_RE = re.compile(
    rf"\bpart[\s\-_]?time{_NOT_FOLLOWED_BY_LETTER}|\bpt\b", re.IGNORECASE
)


# --------------------------------------------------------------------------
# Greenhouse-specific: pick the best raw employment-type label out of a
# company's free-form `metadata` array.
#
# Only ~20% of Greenhouse companies configure a literal "Employment Type"
# custom field. Investigating the real stored metadata (see
# CLAUDE.md/SESSIONS.md) showed a handful of adjacent field names that
# some companies use instead, and whose real values genuinely describe
# employment type:
#   - "Time Type"                    Full-time / Part-time only (Workday's
#                                    own term, surfaced as a GH field)
#   - "Full-time/ Part-time"         the field name IS the concept
#   - "Full-Time/Part-Time Status"   Full-Time / Part-Time only
#   - "Employment Status"            "Regular Full-time", ...
#   - "Work Type"                    Full-time / "Contract, Full-time" / ...
#   - "WORKER_CATEGORY"              Full Time / Contract
#
# Fields investigated and deliberately REJECTED (real values don't map
# onto the Full-time/Part-time/Contract/Internship set):
#   - "Worker Type"    legal classification - "Employee" (~98%), not hours
#   - "Pay Rate Type"  compensation basis - "Salary"/"Hourly"
#   - "Employee Type"  dominated by "Regular" (a job-category value)
#   - "Job Type"       job category - "Standard"/"Pipeline"/"Regular";
#                      also "(PT)" in role names false-triggers Part-time
#
# The literal "Employment Type" field keeps full normalization (an
# unrecognized value there legitimately becomes "Other"). The adjacent
# fallback fields contribute a value only when it resolves to one of the
# four SPECIFIC buckets - if a secondary field normalizes to "Other" that
# is a sign it isn't really describing employment type for that row, so
# it is ignored rather than stored.
_GREENHOUSE_PRIMARY_MARKER = "employment type"
_GREENHOUSE_FALLBACK_FIELDS = (
    "time type",
    "full-time/ part-time",
    "full-time/part-time",
    "full-time/part-time status",
    "employment status",
    "work type",
    "worker_category",
)
_SPECIFIC_TYPES = frozenset({FULL_TIME, PART_TIME, CONTRACT, INTERNSHIP})


def greenhouse_employment_type(metadata_entries) -> str | None:
    """Return the best employment-type label from a Greenhouse job's raw
    `metadata` list, or None if none of the recognized fields carry a
    usable value.

    A literal "Employment Type" entry wins outright and is returned as-is
    for the caller to normalize (it may legitimately become "Other").
    Otherwise the curated adjacent fields are tried in priority order and
    the first whose value resolves to a specific bucket
    (Full-time/Part-time/Contract/Internship) is returned already
    normalized. Re-normalizing that value is idempotent, so callers can
    pass the result straight through ``normalize_employment_type``.
    """
    if not isinstance(metadata_entries, list):
        return None

    primary = None
    fallbacks: dict[str, str] = {}
    for entry in metadata_entries:
        if not isinstance(entry, dict):
            continue
        name = (entry.get("name") or "").strip().lower()
        value = entry.get("value")
        if not value or not isinstance(value, str):
            continue
        if _GREENHOUSE_PRIMARY_MARKER in name:
            primary = value
        elif name in _GREENHOUSE_FALLBACK_FIELDS and name not in fallbacks:
            fallbacks[name] = value

    if primary is not None:
        return primary

    for field in _GREENHOUSE_FALLBACK_FIELDS:
        raw = fallbacks.get(field)
        if raw is None:
            continue
        normalized = normalize_employment_type(raw)
        if normalized in _SPECIFIC_TYPES:
            return normalized
    return None


def normalize_employment_type(raw: str | None) -> str | None:
    """Map one source's raw employment-type label to the normalized set.

    Returns None for a missing/blank raw value (the source gave no real
    signal), one of FULL_TIME/PART_TIME/CONTRACT/INTERNSHIP for a
    recognized label, or OTHER for a real, non-blank label that doesn't
    match any of those (e.g. "Regular", "Employee", "OTHER", "正社員") -
    never None for a real-but-unrecognized value, and never a guessed
    specific bucket for an ambiguous one.
    """
    if raw is None:
        return None
    value = str(raw).strip()
    if not value:
        return None

    if _INTERNSHIP_RE.search(value):
        return INTERNSHIP
    if _CONTRACT_RE.search(value):
        return CONTRACT
    if _FULL_TIME_RE.search(value):
        return FULL_TIME
    if _PART_TIME_RE.search(value):
        return PART_TIME
    return OTHER
