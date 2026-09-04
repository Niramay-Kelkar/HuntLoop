"""
Role-agnostic relevance gate for ``job_postings.is_relevant``.

``is_relevant`` answers one narrow question before resume-matching or
skills-analysis spend effort on a posting: **is this a desk/knowledge-work
role at all, or is it manual / blue-collar / front-line hourly work that
this product has no reason to surface?**

As of 2026-09-03 (see the relevance-gate redesign entry in
``huntloop-architecture-decisions.md`` and SESSIONS.md) this is a pure
**denylist** check:

    is_relevant = the job title does NOT match the manual/blue-collar denylist

Title-only, word-boundary matching. No job-description text is consulted
(that was never validated). No embedding, similarity threshold, or
reference text is involved in the decision anymore.

What changed and why:

* The previous design combined a category ``REFERENCE_TEXT`` embedding
  similarity with two keyword lists (``HARD_EXCLUDE_KEYWORDS`` /
  ``SOFT_EXCLUDE_KEYWORDS``) that structurally blocked whole business
  functions - sales, marketing, HR, legal/tax/accounting, partnerships,
  procurement, and more. That was too aggressive: an "Account Executive",
  "HR Business Partner", or "Tax Manager" at a sponsoring company is a
  legitimate posting a candidate might want to see, and the embedding
  half also produced real false negatives (e.g. "Sr. Forward Deployed
  Engineer - Retail", "Senior Solutions Architect (EDW Enterprise Data
  Warehouse Migrations)" both wrongly excluded by the old
  manufacturing/warehouse/retail hard-excludes).
* The only thing the gate genuinely needs to remove is manual/hourly
  work - warehouse, driving, production line, skilled trades, janitorial,
  food service, retail floor - which is unambiguous from the title alone
  and needs no semantic model. That is exactly what ``DENYLIST_KEYWORDS``
  below encodes. It was hand-validated against real job titles in this
  dataset (see the architecture-decisions entry for the numbers).
* Clinical / healthcare roles were never on the denylist and remain
  included.

``job_postings.embedding`` is still computed and stored at insert time -
it is used by the completely separate query-time resume ``match_score``
mechanism and is untouched by this redesign.

Backward-compatibility notes (do not remove without checking callers):

* ``classify_relevance(title, embedding_similarity=None)`` keeps its old
  two-argument shape. ``huntloop.pipelines`` and
  ``scripts/backfill_relevance.py`` still pass a computed similarity as
  the second argument; it is now **ignored**.
* ``REFERENCE_TEXT`` and ``cosine_similarity`` are retained because
  ``huntloop.pipelines`` and several superseded calibration scripts
  import them at module load. They are no longer part of the
  ``is_relevant`` decision.
* ``HARD_EXCLUDE_KEYWORDS`` / ``SOFT_EXCLUDE_KEYWORDS`` /
  ``EXCLUDE_KEYWORDS`` / ``EMBEDDING_SIMILARITY_THRESHOLD`` /
  ``SOFT_EXCLUDE_RESCUE_THRESHOLD`` are retained as inert module
  constants for the same reason (imports in
  ``scripts/reclassify_soft_excludes.py`` and
  ``scripts/calibrate_soft_exclude_threshold.py``). They are historical.
"""
import re

import numpy as np

# ---------------------------------------------------------------------------
# The denylist. 191 terms: the 190-term hand-validated list plus
# "hoist operator" (added because the "forklift operator" phrase did not
# match "Forklift/Hoist Operator" - an interposed word breaks the phrase).
#
# Matching (see title_matches_denylist / _term_matches):
#   - a term containing a space is matched as a plain substring of the
#     lowercased title ("hoist operator" in "forklift/hoist operator").
#   - any other term is matched with a word boundary in front and, behind
#     it, "not a letter" - so a term immediately followed by a DIGIT still
#     matches ("picker/packer" matches "Picker/Packer2"). A trailing
#     letter still blocks the match ("mason" does not match "masonry").
#
# Grouped only for readability; grouping has no effect on matching.
# ---------------------------------------------------------------------------
DENYLIST_KEYWORDS = [
    # driving / delivery
    "truck driver", "delivery driver", "route driver", "store driver",
    "shuttle driver", "bus driver", "van driver", "forklift driver",
    "school bus", "cdl a", "cdl b", "cdl-a", "cdl-b", "non cdl", "non-cdl",
    "class a driver", "class b driver", "driver helper", "package delivery",
    "delivery associate", "delivery helper", "courier", "transporter",
    "vehicle transporter",

    # warehouse / fulfillment / material movement
    "warehouse", "material handler", "order picker", "order selector",
    "picker packer", "picker/packer", "package handler", "package sorter",
    "sortation", "dock worker", "dockworker", "freight handler",
    "freight associate", "stocker", "loader", "unloader",
    "fulfillment associate", "fulfillment center", "inventory associate",
    "shipping clerk", "receiving clerk", "shipping associate",
    "receiving associate",

    # production / assembly-line / machine operation
    "machine operator", "equipment operator", "forklift operator",
    "hoist operator", "press operator", "production operator",
    "manufacturing operator", "cnc operator", "boiler operator",
    "plant operator", "crane operator", "loader operator", "line operator",
    "process operator", "packaging operator", "assembly operator",
    "warehouse operator", "production worker", "assembly worker",
    "assembly line", "assembly associate", "assembler", "line assembler",
    "mechanical assembler", "electronic assembler", "electronics assembler",
    "fabricator", "press brake", "die setter",

    # skilled trades / hands-on maintenance
    "mechanic", "electrician", "plumber", "pipefitter", "welder",
    "millwright", "machinist", "carpenter", "mason", "bricklayer", "roofer",
    "glazier", "boilermaker", "ironworker", "sheet metal", "drywall",
    "hvac technician", "hvac installer", "maintenance technician",
    "maintenance mechanic", "maintenance worker", "maintenance helper",
    "maintenance associate", "general maintenance", "facilities worker",
    "groundskeeper", "landscaper", "lawn care", "irrigation technician",
    "general laborer", "general labor", "day laborer", "manual labor",
    "field laborer",

    # automotive service-bay
    "automotive technician", "auto technician", "tire technician",
    "lube technician", "brake technician", "oil change", "tire and lube",
    "brake and tire", "auto detailer", "vehicle detailer", "car wash",
    "tire installer", "quick lane", "service lane technician",

    # janitorial / housekeeping / grounds
    "custodian", "custodial", "janitor", "janitorial", "housekeeping",
    "housekeeper", "room attendant", "laundry attendant", "linen attendant",
    "environmental services technician", "evs tech", "evs technician",
    "porter", "cleaner", "cleaning associate",

    # food service / kitchen
    "food service", "line cook", "prep cook", "food prep", "cook ii",
    "cook i", "kitchen helper", "cafeteria", "food runner", "concession",
    "dishwasher", "barista", "busser", "server assistant", "banquet server",
    "catering attendant", "deli associate", "bakery associate",

    # retail floor / front-line store
    "retail associate", "sales associate", "store associate", "retail sales",
    "store clerk", "bagger", "front end associate", "cashier", "checker",
    "merchandiser", "stock associate", "stock clerk", "floor associate",
    "greeter",

    # other manual front-line
    "security guard", "gate guard", "flagger", "mover", "valet",
    "parking attendant", "groundman", "utility worker", "sanitation",
    "sanitation worker", "waste collector", "recycling associate",
    "grounds crew", "crew member", "team member", "seasonal associate",
    "warehouse selector", "meat cutter", "butcher", "produce clerk",
]

# The one denylist term that needs a carve-out: "warehouse" also appears
# in genuinely technical titles as "data warehouse" / "data warehousing"
# (e.g. "Staff Data Warehouse Engineer", "Senior Solutions Architect (EDW
# Enterprise Data Warehouse Migrations)"). When "data warehous" is present
# in the title, the bare "warehouse" term is skipped - but every other
# explicit warehouse term ("warehouse operator", "warehouse selector")
# still applies.
_DATA_WAREHOUSE_MARKER = "data warehous"


def _term_matches(title_lower: str, term: str) -> bool:
    if " " in term:
        return term in title_lower
    # word boundary in front; "not a letter" behind, so a trailing digit
    # (e.g. "Picker/Packer2") does not block the match but a trailing
    # letter (e.g. "masonry") does.
    return re.search(
        r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z])", title_lower
    ) is not None


def title_matches_denylist(title: str) -> bool:
    """True if the job title names manual / blue-collar / front-line work
    per DENYLIST_KEYWORDS."""
    if not title:
        return False
    t = title.lower()
    skip_bare_warehouse = _DATA_WAREHOUSE_MARKER in t
    for term in DENYLIST_KEYWORDS:
        if term == "warehouse" and skip_bare_warehouse:
            continue
        if _term_matches(t, term):
            return True
    return False


def classify_relevance(title: str, embedding_similarity: float | None = None) -> bool:
    """``is_relevant`` verdict for a job title.

    ``is_relevant = NOT title_matches_denylist(title)``.

    ``embedding_similarity`` is accepted only for backward compatibility
    with existing callers (``huntloop.pipelines``,
    ``scripts/backfill_relevance.py``) and is **ignored** - the decision
    is title-only. An empty/None title is treated as relevant (nothing to
    exclude on).
    """
    return not title_matches_denylist(title)


# ---------------------------------------------------------------------------
# Retained for backward-compatible imports only - NOT used by
# classify_relevance. See the module docstring.
# ---------------------------------------------------------------------------

#: Historical. Was the category-embedding reference for the old hybrid
#: filter. ``huntloop.pipelines`` and some calibration scripts still
#: import this name; it no longer influences ``is_relevant``.
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

#: Historical - superseded by DENYLIST_KEYWORDS. These were the old
#: whole-business-function keyword lists. They are inert here now.
HARD_EXCLUDE_KEYWORDS = [
    "sales", "account executive", "account director", "business development",
    "customer support", "customer enablement", "revenue operations",
    "sales operations", "partner manager", "partnerships",
    "marketing", "creative director", "creative sourcer", "brand designer",
    "motion designer", "content designer", "managing editor", "communications",
    "recruiter", "recruiting", "sourcer", "talent", "people partner",
    "people relations", "hr business partner", "human resources", " hr ",
    "workplace operations", "workplace",
    "legal", "counsel", "tax", "accounting", "accountant",
    "executive assistant", "chief of staff", "procurement", "supply chain",
    "business operations", "deal operations", "deal team",
    "fraud operations", "manufacturing", "warehouse", "retail",
]
SOFT_EXCLUDE_KEYWORDS = [
    "customer success",
    "solutions consultant",
]
EXCLUDE_KEYWORDS = HARD_EXCLUDE_KEYWORDS + SOFT_EXCLUDE_KEYWORDS

#: Historical thresholds from the old embedding-similarity gate.
EMBEDDING_SIMILARITY_THRESHOLD = 0.29
SOFT_EXCLUDE_RESCUE_THRESHOLD = 0.335


def cosine_similarity(a, b) -> float:
    """Plain vector-math helper. No longer part of the relevance decision,
    but still imported by ``huntloop.pipelines`` (which computes and
    stores ``job_postings.embedding``) and by
    ``scripts/backfill_relevance.py`` / the calibration scripts."""
    a, b = np.array(a), np.array(b)
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    if denom == 0:
        return 0.0
    return float(np.dot(a, b) / denom)
