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
EXCLUDE_KEYWORDS = [
    # sales / GTM
    "sales", "account executive", "account director", "business development",
    "solutions consultant", "customer success", "customer support",
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


def keyword_exclude_match(title: str) -> bool:
    """True if the title contains a keyword indicating a non-technical
    business function - an override signal (see classify_relevance)."""
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
    """
    if keyword_exclude_match(title):
        return False
    return keyword_include_match(title) or embedding_similarity >= EMBEDDING_SIMILARITY_THRESHOLD
