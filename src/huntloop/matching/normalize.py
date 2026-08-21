"""
Mechanical employer-name normalization for cross-file/cross-quarter LCA
disclosure matching (see Step 1's audit of DOL's raw EMPLOYER_NAME
formatting: inconsistent casing, punctuation, and trailing legal-entity
suffixes for what is otherwise the same employer).

This is intentionally mechanical, not full entity resolution - it does not
attempt to deduplicate genuinely different companies. See tests for the
"false positive risk" case this is meant to avoid.
"""

import re

# Trailing legal-entity suffixes to strip, after uppercasing and stripping
# periods/commas - so punctuated variants like "L.L.C." already collapse to
# "LLC" by the time this set is checked.
_LEGAL_SUFFIXES = {"INC", "LLC", "LLP", "LP", "CORP", "CO", "LTD", "PLLC", "PC"}

_PUNCTUATION_RE = re.compile(r"[.,]")
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_employer_name(name: str) -> str:
    """Uppercase, strip periods/commas, collapse whitespace, and drop a
    trailing legal-entity suffix. Suffixes are only stripped when they are
    the last word - the same word appearing mid-name is left alone."""
    value = name.upper()
    value = _PUNCTUATION_RE.sub("", value)
    value = _WHITESPACE_RE.sub(" ", value).strip()

    words = value.split(" ") if value else []
    while len(words) > 1 and words[-1] in _LEGAL_SUFFIXES:
        words.pop()
    return " ".join(words)
