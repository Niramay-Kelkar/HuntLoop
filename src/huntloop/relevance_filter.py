"""
Hybrid keyword + embedding-similarity relevance pre-filter for
job_postings.is_relevant. Runs before resume-matching or skills-analysis
so effort isn't spent scoring/matching postings that aren't
software-engineering/technical roles at all - see
scripts/backfill_relevance.py for the batch job that applies this.

Keyword lists below were built by reading every one of the 606 real
job_title values in this project's dataset at the time this module was
written (2026-08-24, see SESSIONS.md), not guessed generically. Several
words from a "generic" first-pass list were deliberately left out
because the real data showed them to be unreliable signals here:

- "analyst" alone: real titles show it's mostly non-technical in this
  dataset (FP&A Analyst, Financial Intelligence Analyst, Mobility Tax
  Analyst, Talent Intelligence Analyst, Workplace Operations Analyst,
  Anti-Money Laundering Monitoring Analyst) - only one borderline
  exception (Site Reliability Operations Analyst) exists, and that one
  still matches on "site reliability". Including "analyst" would have
  pulled in far more false positives than it caught.
- "technical" alone: real titles show it's just as often a qualifier on
  a non-engineering role (Technical Recruiter, Technical Sourcer,
  Technical Accounting Manager, Technical Account Manager, Technical
  Quality Specialist) as an engineering one. Left to the embedding
  signal instead.

INCLUDE_KEYWORDS and EXCLUDE_KEYWORDS are matched as whole words/phrases
against the lowercased job title (see _contains_any below) - not
substring matches, so e.g. "ai" doesn't match inside "maintain".
"""
import re

import numpy as np

# Real title substrings observed in this dataset that indicate a
# software-engineering/technical role: engineer/engineering (by far the
# dominant signal - Software/Backend/Frontend/DevOps/Platform/Security/
# Infrastructure/Site Reliability/Forward Deployed/iOS/Android/AI
# Research Engineer, etc.), developer (Web Application Developer, Mixed
# Reality Developer), software, data engineer/data scientist/data
# science (Data Engineer, Data Scientist - Finance/Core Data, Director/
# Manager/Senior Data Science ...), scientist (AI Applied Scientist),
# devops, site reliability, infrastructure, backend, frontend,
# full stack, machine learning, and cloud.
INCLUDE_KEYWORDS = [
    "engineer",
    "engineering",
    "developer",
    "software",
    "devops",
    "site reliability",
    "infrastructure",
    "backend",
    "frontend",
    "front end",
    "full stack",
    "machine learning",
    "data scientist",
    "data science",
    "scientist",
    "cloud",
]

# Real non-technical title substrings observed in this dataset, grouped
# by the business function they signal. Deliberately phrase-level where
# a bare word would be too broad (e.g. "operations" alone would also
# match "Site Reliability Operations Analyst" - a real tech-adjacent
# title - so specific operations phrases are used instead).
#
# HARD excludes: an absolute override - a title containing one of these
# is not relevant regardless of any other signal. Strong existing
# evidence these are mostly right (e.g. "sales": 241/253 "Sales
# Engineer" titles correctly excluded - see SESSIONS.md 2026-08-30
# diagnostic). "sales" is a candidate for a future soft-exclude look if
# contradicting evidence turns up, but is deliberately NOT changed here.
HARD_EXCLUDE_KEYWORDS = [
    # sales / GTM
    "sales", "account executive", "account director", "business development",
    "customer support",
    "customer enablement", "revenue operations", "sales operations",
    "partner manager", "partnerships",
    # marketing / brand / content
    "marketing", "creative director", "creative sourcer", "brand designer",
    "motion designer", "content designer", "managing editor",
    "communications",
    # people / talent / HR
    "recruiter", "recruiting", "sourcer", "talent", "people partner",
    "people relations", "hr business partner", "human resources", " hr ",
    "workplace operations", "workplace",
    # legal / tax / accounting. Deliberately does NOT include "finance"/
    # "financial": those bare words would also override "Data Scientist,
    # Finance" (a real, genuinely technical title in this dataset) to
    # excluded, since exclude always wins. The non-technical finance
    # titles this would otherwise catch (Strategic Finance, Financial
    # Intelligence Analyst, Product Finance & Strategy Manager, Senior
    # Corporate Financial Planning & Strategy Analyst, Senior Strategic
    # Finance Manager) contain no include keyword either, so they still
    # correctly fall through to the embedding signal instead - no false
    # positive from dropping these two words, but keeping them would
    # have caused a real false negative.
    "legal", "counsel", "tax", "accounting", "accountant",
    # other clearly non-technical business functions seen in this data
    "executive assistant", "chief of staff", "procurement", "supply chain",
    "business operations", "deal operations", "deal team",
    "fraud operations", "manufacturing", "warehouse", "retail",
]

# SOFT excludes (added 2026-08-30, see SESSIONS.md +
# scripts/calibrate_soft_exclude_threshold.py): these two phrases sit on
# both sides of the technical/non-technical line in the real data -
# "customer success" catches both Rubrik's non-technical "Customer
# Success Engineer" (post-deployment support) AND Palantir's genuinely
# technical "Forward Deployed Enablement Engineer - Customer Success";
# "solutions consultant" catches non-technical pre-sales AND Figma's
# "Enterprise Solutions Consultant" (deep technical engagement with
# engineering audiences). A soft-exclude title is excluded ONLY IF its
# category-reference-text embedding similarity is also below
# SOFT_EXCLUDE_RESCUE_THRESHOLD (see classify_relevance). The include
# keyword is deliberately NOT consulted for these - "Customer Success
# Engineer" contains "engineer" too, and must stay excluded.
SOFT_EXCLUDE_KEYWORDS = [
    "customer success",
    "solutions consultant",
]

# Back-compat: the full exclude set, for any caller that just wants
# "is this an exclude-listed title at all". classify_relevance uses the
# hard/soft split, not this.
EXCLUDE_KEYWORDS = HARD_EXCLUDE_KEYWORDS + SOFT_EXCLUDE_KEYWORDS

# Describes the *category* of role (software engineering / technical
# work), not any specific person's background - deliberately distinct
# from a resume, since this filter must work the same regardless of
# which resume is active. Covers the breadth actually seen in the
# real "relevant" side of this dataset (product SWE, infra/platform,
# SRE/DevOps, security engineering, data engineering, ML/AI engineering)
# so it doesn't just match one narrow flavor of "engineer".
REFERENCE_TEXT = (
    "Software engineering and technical engineering roles. Designing, "
    "building, testing, and maintaining software systems and "
    "applications. Writing code in languages such as Python, Java, "
    "C++, Go, or JavaScript. Building backend services, APIs, "
    "distributed systems, and frontend or mobile applications. "
    "Designing and operating cloud infrastructure, CI/CD pipelines, "
    "and production systems as a DevOps, site reliability, or platform "
    "engineer. Securing systems and data as a security or information "
    "security engineer. Building data pipelines, data platforms, and "
    "machine learning or AI systems as a data engineer, machine "
    "learning engineer, or applied/research scientist. Solving "
    "engineering problems involving algorithms, system design, "
    "databases, networking, and scalability."
)

_WORD_BOUNDARY_KEYWORDS = {"scientist", "cloud", "software", "developer"}


def _contains_any(title_lower: str, keywords: list[str]) -> bool:
    for kw in keywords:
        if " " in kw.strip():
            if kw in title_lower:
                return True
        else:
            if re.search(r"\b" + re.escape(kw.strip()) + r"\b", title_lower):
                return True
    return False


def keyword_include_match(title: str) -> bool:
    """True if the title contains a keyword indicating a technical role."""
    return _contains_any(title.lower(), INCLUDE_KEYWORDS)


def keyword_hard_exclude_match(title: str) -> bool:
    """True if the title contains a HARD-exclude keyword - an absolute
    override that always wins (see classify_relevance)."""
    return _contains_any(title.lower(), HARD_EXCLUDE_KEYWORDS)


def keyword_soft_exclude_match(title: str) -> bool:
    """True if the title contains a SOFT-exclude keyword ("customer
    success" / "solutions consultant") - excluded only if the category
    embedding similarity is also below SOFT_EXCLUDE_RESCUE_THRESHOLD."""
    return _contains_any(title.lower(), SOFT_EXCLUDE_KEYWORDS)


def keyword_exclude_match(title: str) -> bool:
    """True if the title contains any exclude keyword (hard OR soft).
    Back-compat helper - classify_relevance uses the hard/soft split, not
    this. Kept for callers that just want "is this exclude-listed at all"."""
    return _contains_any(title.lower(), EXCLUDE_KEYWORDS)


# Chosen from real measured cosine similarities (all-MiniLM-L6-v2,
# REFERENCE_TEXT vs. real title+description text) against this dataset's
# actual known-relevant/known-irrelevant rows - see
# scripts/calibrate_relevance_threshold.py and SESSIONS.md for the full
# run. Measured similarities, 2026-08-24:
#   known-irrelevant: Fraud Operations Specialist 0.1770, Chief of Staff
#     0.2547, Creative Director 0.1636 (max 0.2547)
#   known-relevant: Software Engineer - Apollo Platform 0.3910, Software
#     Engineer - Core Interfaces 0.3240, Software Engineer - Defense
#     Applications 0.4610, Senior Data Science Manager 0.3618, Platform
#     Engineer - Identity Infrastructure 0.4056 (min 0.3240)
# Clean separation with no overlap - any value in (0.2547, 0.3240) splits
# these examples correctly. 0.29 is the midpoint, giving roughly equal
# margin on both sides rather than hugging either boundary.
EMBEDDING_SIMILARITY_THRESHOLD = 0.29

# Rescue threshold for SOFT_EXCLUDE_KEYWORDS (see that list + classify_relevance).
# A "customer success" / "solutions consultant" titled role is excluded
# only if its category-reference-text embedding similarity is BELOW this.
#
# Calibrated 2026-08-30 from the real category similarities of ALL 318
# "customer success" / "solutions consultant" titled job_postings rows
# (scripts/calibrate_soft_exclude_threshold.py). Set HIGHER than
# EMBEDDING_SIMILARITY_THRESHOLD (0.29): a title that actively names a
# customer-success / pre-sales function needs a stronger semantic signal
# to overcome that than a neutrally-worded title does. The three real
# rows this must get right (task-specified), with their measured
# category similarities:
#   Rubrik "Senior Customer Success Engineer"          0.3232  -> keep EXCLUDED
#     (genuine post-deployment technical *support*, not building)
#   Figma "Enterprise Solutions Consultant"            0.3403  -> RESCUE
#     ("go deep technically with an engineering and product audience")
#   Palantir "Forward Deployed Enablement Engineer     0.4272- -> RESCUE
#     - Customer Success"                              0.4394
#     (builds tooling/infra, debugs nebulous technical issues)
# Usable window is therefore (0.3232, 0.3403]. 0.335 sits in the widest
# real gap in that window (0.3330 -> 0.3363, the largest spacing between
# adjacent sorted similarities in the 0.32-0.34 region), giving ~0.012
# margin above Rubrik and ~0.005 below Figma. At 0.335, 27 of the 318
# rows flip to relevant (23 distinct titles) - ~19 genuinely technical
# customer-facing/pre-sales-engineering roles + ~4 residual "Manager"/
# "Program Manager"/"Ops Analyst" false positives, accepted as MVP noise
# the same way "GRC Program Manager"/"Product Designer" are for the base
# filter. The other 282 (Samsara/Planet/Datadog/Okta "Customer Success
# Manager", low-similarity "Solutions Consultant") correctly stay
# excluded. Full table + reasoning in SESSIONS.md 2026-08-30.
SOFT_EXCLUDE_RESCUE_THRESHOLD = 0.335


def cosine_similarity(a, b) -> float:
    """Plain vector-math helper, not part of the tuned classification
    logic itself - shared by scripts/backfill_relevance.py,
    scripts/calibrate_relevance_threshold.py, and
    huntloop.pipelines.JobDataPipeline so there's one implementation,
    not three copies."""
    a, b = np.array(a), np.array(b)
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    if denom == 0:
        return 0.0
    return float(np.dot(a, b) / denom)


def classify_relevance(title: str, embedding_similarity: float) -> bool:
    """Combine the keyword and embedding signals into a single
    is_relevant verdict.

    Logic: (keyword-include OR embedding-similarity >= threshold) AND
    NOT keyword-exclude.

    Keyword-exclude is an override that always wins, even over a
    keyword-include or high embedding similarity: a title explicitly
    naming a non-technical business function (legal, sales, marketing,
    tax, recruiting, ...) is a stronger, more specific signal than a
    generic word like "engineer" appearing elsewhere in the same title
    (e.g. "Embedded Legal Engineer", "Marketing Engineer") or than a
    moderate embedding similarity, which is a coarser, dataset-wide
    signal. Keyword-include and embedding-similarity are combined with
    OR (not AND) so that either a strong keyword match or a strong
    semantic match is sufficient - keywords alone would miss real
    technical titles that don't happen to use any listed word (e.g. a
    role titled just "iOS Engineer" is already caught by "engineer", but
    something more obliquely worded wouldn't be), and embedding
    similarity alone would need a very conservative threshold to avoid
    false positives on its own, given the moderate similarity many
    generic corporate titles show to any reference text.

    SOFT excludes ("customer success", "solutions consultant") are a
    middle ground: checked after the hard excludes, they exclude the
    title ONLY IF the embedding similarity is also below
    SOFT_EXCLUDE_RESCUE_THRESHOLD. This rescues genuinely technical
    customer-facing engineering roles that happen to carry one of those
    phrases while still excluding the non-technical support/pre-sales
    roles that share it. The include keyword is deliberately not
    consulted here (a "Customer Success Engineer" contains "engineer"
    too and must stay excluded).
    """
    if keyword_hard_exclude_match(title):
        return False
    if keyword_soft_exclude_match(title):
        return embedding_similarity >= SOFT_EXCLUDE_RESCUE_THRESHOLD
    return keyword_include_match(title) or embedding_similarity >= EMBEDDING_SIMILARITY_THRESHOLD
