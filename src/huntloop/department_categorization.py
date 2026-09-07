"""Map each source's raw, free-text ``job_postings.department`` string onto
a small controlled vocabulary of canonical categories - the way an ATS
platform (Greenhouse, Lever, Workday) groups postings into a handful of
"departments" regardless of what the hiring company literally typed.

Why this exists
---------------
``department`` is genuinely messy free text. The real live data (see
CLAUDE.md/SESSIONS.md for the full investigation) has ~4,800 distinct
non-NULL values across ~55k postings:

  - a clean head: "Engineering" (3,156), "Sales" (2,642), "Marketing",
    "Finance", "Product", "Legal", "IT", ...
  - a long, company-specific tail: "R&D - Backend Infra",
    "20213 S&M - Sales - Square Outside", "SW Eng - Core Identity-670",
    "Voice & Infrastructure - Product Engineering", numeric requisition
    codes, non-English labels ("Steuerberatung"), and industry-vertical
    labels that name no function at all ("Real estate", "Energy and
    natural resources").
  - genuinely unclassifiable values: "Reconditioning", "Woven City",
    "CPXO", "Ω ARCHIVE - Do not remove", "zz-Evergreen Requisition".

This is NOT a single tech company's data - it spans ~700 employers
(hospitals, universities, manufacturers, construction consultancies,
staffing firms), so the taxonomy below is deliberately broader than a
generic Engineering/Product/Sales/Marketing list.

Cost model
----------
Categorization maps DISTINCT STRINGS, not per-posting - so the total
work is bounded by the ~4,800 distinct values, not the ~99k rows. A
rule-based keyword pass resolves the overwhelming majority of postings
(the clean head plus any tail value containing a recognizable function
word); only the residual distinct values that rules can't confidently
place go to the LLM pass. Anything the LLM also can't place maps to
"Other" - never a guess.

Contract
--------
``rule_based_category(raw) -> str | None``
    A canonical category, or None if no rule fires with confidence.
    Pure, deterministic, no network - safe to call at insert time in the
    pipeline (same pattern as huntloop.employment_type).

``categorize_values(values) -> dict[str, str]``
    Full pass over a list of distinct raw strings: rules first, LLM for
    the remainder, "Other" for whatever is left. Used by
    scripts/backfill_department_category.py.

"Other" is a real bucket meaning "a real department string that names no
function we categorize" - it is not the same as NULL (NULL = the posting
has no raw department at all).
"""
from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)

# --- Canonical taxonomy ---------------------------------------------------
# Chosen from the REAL distinct values found in the live data, not a
# generic template. See the module docstring and SESSIONS.md.
ENGINEERING = "Engineering"
DATA_ANALYTICS = "Data & Analytics"
PRODUCT = "Product"
DESIGN = "Design"
IT = "IT"
SALES = "Sales"
MARKETING = "Marketing"
CUSTOMER_SUPPORT = "Customer Support"
OPERATIONS = "Operations"
FINANCE = "Finance & Accounting"
LEGAL = "Legal & Compliance"
PEOPLE_HR = "People & HR"
HEALTHCARE = "Healthcare & Clinical"
RESEARCH_SCIENCE = "Research & Science"
MANUFACTURING = "Manufacturing & Production"
CONSTRUCTION_TRADES = "Construction & Skilled Trades"
CONSULTING = "Consulting & Professional Services"
EXECUTIVE = "Executive & General Management"
OTHER = "Other"

CANONICAL_CATEGORIES: tuple[str, ...] = (
    ENGINEERING,
    DATA_ANALYTICS,
    PRODUCT,
    DESIGN,
    IT,
    SALES,
    MARKETING,
    CUSTOMER_SUPPORT,
    OPERATIONS,
    FINANCE,
    LEGAL,
    PEOPLE_HR,
    HEALTHCARE,
    RESEARCH_SCIENCE,
    MANUFACTURING,
    CONSTRUCTION_TRADES,
    CONSULTING,
    EXECUTIVE,
    OTHER,
)

# --- Rule-based keyword mapping -----------------------------------------
# Ordered list of (compiled regex, category). The FIRST match wins, so
# more specific / higher-confidence signals are listed before broader
# ones (e.g. "data engineer" -> Data & Analytics before the generic
# "engineer" -> Engineering rule; "sales engineer" -> Sales before
# "engineer").
#
# Patterns are matched case-insensitively against the raw string with
# \y (POSIX word boundary - Python `re` uses \b, handled below) so a
# substring like "it" only matches the standalone token, not "unit".
def _rx(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


_RULES: list[tuple[re.Pattern[str], str]] = [
    # --- disambiguation: specific roles that contain a broader word ---
    (_rx(r"\bsales\s+engineer|\bpre[\s-]?sales|\bsolutions?\s+engineer|\bsolutions?\s+consult"), SALES),
    (_rx(r"\bdata\s+(engineer|platform)|\bml\s+(engineer|ops)|\bmlops\b|\banalytics\s+engineer"), DATA_ANALYTICS),
    (_rx(r"\bdeveloper\s+relations|\bdevrel\b|\bdeveloper\s+advocacy"), MARKETING),
    (_rx(r"\btechnical\s+program\s+manage|\btpm\b|\bprogram\s+management\s*-\s*(eng|product)"), PRODUCT),
    (_rx(r"\bclinical\s+research|\bclinical\s+trial|\bbiostatistic|\bclinical\s+data"), RESEARCH_SCIENCE),
    (_rx(r"\bclinical\s+(engineer|tech)|\bbiomedical\s+(engineer|equipment)"), HEALTHCARE),
    (_rx(r"\bsales\s+operations|\brevenue\s+operations|\brev\s?ops\b|\bgtm\s+operations"), OPERATIONS),
    (_rx(r"\bpeople\s+operations|\bhr\s+operations"), PEOPLE_HR),
    (_rx(r"\bmarketing\s+operations|\bmops\b"), MARKETING),
    (_rx(r"\bfield\s+engineer|\bfield\s+service|\bfield\s+ops|\bfield\s+operation"), CONSTRUCTION_TRADES),
    # --- Engineering ---
    (_rx(
        r"\bengineer(ing)?\b|\bsoftware\b|\bdeveloper\b|\bdevelopment\s*-\s*(back|front|full|mobile|web|platform)"
        r"|\bdevops\b|\bsre\b|\bsite\s+reliability|\binfrastructure\b|\bplatform\b|\bbackend\b|\bfront[\s-]?end\b"
        r"|\bfull[\s-]?stack\b|\bfirmware\b|\bembedded\b|\barchitecture\b|\barchitect\b|\bqa\b|\bquality\s+assurance"
        r"|\bsdet\b|\btest\s+automation|\bmobile\b|\bweb\s+dev|\bapplication\s+development|\bcloud\b|\bsecurity\s+engineer"
        r"|\bappsec\b|\bcybersecurity\b|\binformation\s+security|\binfosec\b|\bdev\s+eng\b|\br&d\s*-\s*(back|front|infra|eng)"
        r"|\bsw\s+eng|\bhw\s+eng|\bs/?w\s+eng|\bh/?w\s+eng|\beng\s*-\s*(infra|platform|core|backend|frontend)"
    ), ENGINEERING),
    # --- Data & Analytics ---
    (_rx(
        r"\bdata\s+(science|scientist|analytics|analyst|governance|strategy)\b|\banalytics\b|\bbusiness\s+intelligence\b"
        r"|\bbi\s+team\b|\bmachine\s+learning\b|\bartificial\s+intelligence\b|\bapplied\s+ai\b|\bapplied\s+ml\b"
        r"|\bdata\s+team\b|\bdecision\s+science|\bquantitative\s+research"
    ), DATA_ANALYTICS),
    # --- Product ---
    (_rx(r"\bproduct\s+manage|\bproduct\s+team\b|\bproduct\s+owner|\bproduct\b(?!\s*(marketing|design))|\bproduct$"), PRODUCT),
    # --- Design ---
    (_rx(
        r"\bdesign\b|\bux\b|\bui\b|\buser\s+experience|\buser\s+research|\bcreative\b|\bgraphic\b|\bbrand\s+design"
        r"|\bproduct\s+design|\bcontent\s+design|\bmotion\s+design|\bart\b|\bindustrial\s+design"
    ), DESIGN),
    # --- IT (internal / corporate tech, distinct from product engineering) ---
    (_rx(
        r"\binformation\s+technology\b|\bit\b|\bit\s*&?\s*(security|ops|support|infrastructure)|\bhelp\s?desk\b"
        r"|\bservice\s+desk\b|\bend\s?user\s+(support|computing)|\bsystems?\s+admin|\bnetwork\s+admin"
        r"|\bcorporate\s+(it|engineering|systems)|\benterprise\s+(applications|systems|technology)|\bbusiness\s+systems"
    ), IT),
    # --- Sales ---
    (_rx(
        r"\bsales\b|\baccount\s+exec|\bae\b|\bbdr\b|\bsdr\b|\bbusiness\s+development\b|\bbiz\s?dev\b|\brevenue\b"
        r"|\bgo[\s-]?to[\s-]?market\b|\bgtm\b|\baccount\s+manage|\bpartnerships?\b|\bchannel\s+(sales|partner)"
        r"|\bcommercial\b|\benterprise\s+(sales|business)|\binside\s+sales|\bfield\s+sales|\bnew\s+business"
        r"|\brelationship\s+manage|\bclient\s+partner|\bsales\s+development"
    ), SALES),
    # --- Marketing ---
    (_rx(
        r"\bmarketing\b|\bbrand\b|\bcommunications?\b|\bcomms\b|\bgrowth\b|\bdemand\s+gen|\blead\s+gen"
        r"|\bcontent\b|\bseo\b|\bsem\b|\bpaid\s+(media|search|social)|\bpublic\s+relations\b|\bpr\s+team\b"
        r"|\bsocial\s+media|\bevents?\b|\bfield\s+marketing|\bproduct\s+marketing|\bpmm\b|\bcopywrit"
        r"|\bcampaign|\bmerchandising\b|\bpublishing\b|\bdigital\s+marketing"
    ), MARKETING),
    # --- Customer Support ---
    (_rx(
        r"\bcustomer\s+(success|support|service|care|experience|advocacy)\b|\bclient\s+(success|services?|support)\b"
        r"|\bsupport\b|\bhelp\s+center|\btechnical\s+support|\bsupport\s+engineer|\bcx\b|\bcustomer\s+operations"
        r"|\bpartner\s+success|\bcall\s+center|\bcontact\s+center"
    ), CUSTOMER_SUPPORT),
    # --- Finance & Accounting ---
    (_rx(
        r"\bfinance\b|\bfinancial\b|\baccounting\b|\baccounts?\s+(payable|receivable)\b|\baudit\b|\btax\b"
        r"|\btreasury\b|\bfp&a\b|\bcontroller\b|\bbookkeep|\bpayroll\b|\binvestor\s+relations|\bcorporate\s+development"
        r"|\bstrategic\s+finance|\binvestment\b|\bportfolio\s+manage|\bunderwriting\b|\bactuarial\b|\bbilling\b"
        r"|\bsteuerberatung\b|\bwirtschaftsprüfung\b|\bbanking\b|\bcapital\s+markets"
    ), FINANCE),
    # --- Legal & Compliance ---
    (_rx(
        r"\blegal\b|\bcounsel\b|\bcompliance\b|\bregulatory\b|\bprivacy\b|\bgovernance\b|\brisk\b|\bethics\b"
        r"|\bparalegal\b|\bcontracts?\s+manage|\bip\s+team\b|\bintellectual\s+property|\bgrc\b"
    ), LEGAL),
    # --- People & HR ---
    (_rx(
        r"\bhuman\s+resources\b|\bhr\b|\bpeople\b|\bpeople\s*&?\s*culture|\btalent\b|\brecruit|\bstaffing\b"
        r"|\btalent\s+acquisition|\bhris\b|\blearning\s*&?\s*development|\bl&d\b|\bemployee\s+experience"
        r"|\bcompensation\s*&?\s*benefits|\btotal\s+rewards|\bdiversity\b|\bdei\b|\bworkplace\b"
    ), PEOPLE_HR),
    # --- Healthcare & Clinical ---
    (_rx(
        r"\bclinical\b|\bnursing\b|\bnurse\b|\brn\b|\blpn\b|\blvn\b|\bcna\b|\bphysician\b|\bprovider\b|\bmd\b"
        r"|\bpatient\b|\bmedical\b|\bhealthcare\b|\bhealth\s+services|\bpharmacy\b|\bpharmac|\btherap(y|ist|ies)\b"
        r"|\bradiolog|\bsurg(ery|ical)|\bdirect\s+care\b|\baide\b|\bcaregiver\b|\bhome\s+care\b|\bbehavioral\s+health"
        r"|\ballied\s+health|\bphlebotom|\brespiratory\s+(care|therap)|\bfaculty/provider\b|\bcare\s+team\b"
        r"|\bphysical\s+therap|\boccupational\s+therap|\bdental\b|\bhospice\b|\bacute\s+care\b|\bicu\b|\ber\s+nurse"
    ), HEALTHCARE),
    # --- Research & Science ---
    (_rx(
        r"\bresearch\b|\br&d\b|\bresearch\s*&?\s*development\b|\blaborator|\blab\b|\bscience\b|\bscientist\b"
        r"|\bscientific\b|\btesting\s*&?\s*laboratory|\blab\s+testing|\bassay\b|\bchemistry\b|\bbiology\b"
        r"|\bpreclinical\b|\bpharmacolog|\bbioinformatic|\bgenomic|\btranslational\b|\bdiscovery\b(?!\s+phase)"
    ), RESEARCH_SCIENCE),
    # --- Manufacturing & Production ---
    (_rx(
        r"\bmanufactur|\bproduction\b|\bassembly\b|\bfabrication\b|\bmachining\b|\bmachine\s+shop|\bshop\s+floor"
        r"|\breconditioning\b|\bpost[\s-]?production\b|\bwarehouse\s+production|\bplant\s+(operations|manage)"
        r"|\bprocess\s+engineer|\bindustrial\s+engineer|\bpackaging\b|\bfoundry\b|\bwelding\b(?!\s+inspect)"
    ), MANUFACTURING),
    # --- Construction & Skilled Trades ---
    (_rx(
        r"\bconstruction\b|\bcivil\s+engineer|\bstructural\s+engineer|\bmep\b|\bproject\s+controls\b|\bquantity\s+survey"
        r"|\bcost\s+manage|\bskilled\s+trades?\b|\belectrician\b|\bplumb(er|ing)\b|\bhvac\b|\bcarpent"
        r"|\bmaintenance\b|\bfacilities\b|\bcustodi|\bjanitor|\bgrounds\s?keep|\binstallation\b|\bfield\s+install"
        r"|\binspection\b|\binspector\b|\bsurvey(or|ing)\b|\butilit(y|ies)\b|\breal\s+estate\b|\bproperty\s+manage"
        r"|\benergy\s+and\s+natural\s+resources\b|\bnatural\s+resources\b|\bmining\b|\boil\s*&?\s*gas\b|\brenewables?\b"
        r"|\bfleet\b|\bdriver\b|\bdelivery\s+driver|\btransportation\b(?!\s+manage)|\bdispatch\b"
    ), CONSTRUCTION_TRADES),
    # --- Consulting & Professional Services ---
    (_rx(
        r"\bconsult(ing|ant|ancy)\b|\badvisory\b|\bprofessional\s+services\b|\bmanaged\s+services\b"
        r"|\bimplementation\b|\bclient\s+delivery|\bengagement\s+manage|\bsolution\s+delivery|\bprofessional\b$"
        r"|\bbusiness\s+consult"
    ), CONSULTING),
    # --- Operations (broad; incl. admin, supply chain, logistics, biz ops) ---
    (_rx(
        r"\boperations?\b|\bops\b|\blogistics\b|\bsupply\s+chain\b|\bprocurement\b|\bpurchasing\b|\bsourcing\b"
        r"|\bfulfillment\b|\bwarehouse\b|\binventory\b|\bdistribution\b|\bbusiness\s+operations\b|\bbizops\b"
        r"|\bstrategy\s*&?\s*operations|\bmarket\s+operations|\badmin(istrative|istration)?\b|\bclerical\b"
        r"|\boffice\s+manage|\bexecutive\s+assistant|\bfront\s+desk|\breception|\bprogram\s+operations"
        r"|\bvendor\s+manage|\bcategory\s+manage"
    ), OPERATIONS),
    # --- Executive & General Management ---
    (_rx(
        r"\bexecutive\b|\bgeneral\s+manage|\bgm\b|\bleadership\b|\bc-?suite\b|\bchief\s+of\s+staff\b|\bcorporate\b$"
        r"|\bhq\s+manage|\bgeneral\s+&?\s*admin|\bg&a\b|\bboard\s+of\s+directors|\bpresident'?s?\s+office"
    ), EXECUTIVE),
]

# Python `re` uses \b for word boundary, not \y - normalize the patterns
# above (written with \b already, but keep this hook for clarity).


def rule_based_category(raw: str | None) -> str | None:
    """Return a canonical category for ``raw`` from the keyword rules, or
    None when no rule fires. Pure and deterministic - safe to call at
    insert time."""
    if raw is None:
        return None
    value = str(raw).strip()
    if not value:
        return None
    for pattern, category in _RULES:
        if pattern.search(value):
            return category
    return None


# --- LLM-assisted pass for the residual tail ---------------------------
_LLM_SYSTEM_PROMPT = (
    "You categorize job-posting department labels into a fixed taxonomy. "
    "You are given a JSON array of raw department strings scraped from many "
    "different companies' applicant tracking systems. For EACH input string, "
    "choose the single best-fitting category from this exact list:\n"
    + "\n".join(f"- {c}" for c in CANONICAL_CATEGORIES)
    + "\n\nRules:\n"
    "- Use the department label's meaning only. Many labels are company-specific "
    "or contain requisition codes, team names, or non-English words - infer the "
    "function where you reasonably can.\n"
    "- If a label names an industry/vertical but no function (e.g. 'Real estate', "
    "'Energy and natural resources'), pick the closest category for the work that "
    "vertical implies, or 'Other' if truly indeterminate.\n"
    "- If a label is genuinely unclassifiable (pure codes, 'Evergreen Requisition', "
    "'Archive', a bare location, a product name), use 'Other'. Never guess wildly.\n"
    "- 'IT' means internal/corporate technology (helpdesk, sysadmin, business "
    "systems); product/software engineering is 'Engineering'.\n\n"
    'Respond with ONLY a JSON object mapping each input string to its category, '
    'e.g. {"R&D - Backend Infra": "Engineering", "Woven City": "Other"}. '
    "Every input string must appear exactly once as a key."
)

_LLM_BATCH_SIZE = 40
_VALID = frozenset(CANONICAL_CATEGORIES)


def _llm_providers():
    """Yield (name, callable(system, user) -> str|None) for each configured
    provider, in the same order/priority the skills-matching router uses
    (Groq gpt-oss-120b, Groq gpt-oss-20b, Gemini). Imported lazily so a
    missing API key for an unused provider doesn't break importing this
    module or the pipeline."""
    import os

    chain = os.getenv("SKILLS_MATCHING_PROVIDERS", "groq_120b,groq,gemini").split(",")
    for name in [c.strip() for c in chain if c.strip()]:
        try:
            if name == "groq_120b":
                from huntloop import skills_matching_groq_120b as m

                yield name, lambda s, u, m=m: _groq_call(m, s, u)
            elif name == "groq":
                from huntloop import skills_matching as m

                yield name, lambda s, u, m=m: _groq_call(m, s, u)
            elif name == "gemini":
                from huntloop import skills_matching_gemini as m

                yield name, lambda s, u, m=m: m._generate(s, u)
        except Exception as e:  # pragma: no cover - defensive
            logger.warning("department categorization: provider %s unavailable: %s", name, e)


def _groq_call(module, system_prompt: str, user_content: str) -> str | None:
    try:
        resp = module._get_client().chat.completions.create(
            model=module.MODEL_NAME,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
        )
        return resp.choices[0].message.content
    except Exception as e:
        logger.warning("department categorization: Groq call failed: %s", e)
        return None


def _parse_llm_map(raw_text: str | None, expected: list[str]) -> dict[str, str]:
    if not raw_text:
        return {}
    try:
        obj = json.loads(raw_text)
    except json.JSONDecodeError:
        logger.warning("department categorization: LLM response not JSON: %r", raw_text[:200])
        return {}
    if not isinstance(obj, dict):
        return {}
    out: dict[str, str] = {}
    for key in expected:
        cat = obj.get(key)
        if isinstance(cat, str) and cat in _VALID:
            out[key] = cat
    return out


def llm_categorize(values: list[str]) -> tuple[dict[str, str], set[str]]:
    """Categorize each string in ``values`` via the LLM provider chain,
    batched. Returns ``(mapping, answered)`` where ``mapping`` is the
    subset resolved to a canonical category and ``answered`` is every
    value for which SOME provider returned a usable batch response (so a
    value in ``answered`` but not in ``mapping`` is one the model saw and
    could not place -> a legitimate "Other"; a value in neither was never
    successfully seen, e.g. every provider was quota-exhausted, and
    should be retried on a later run rather than forced to "Other").
    Never raises."""
    result: dict[str, str] = {}
    answered: set[str] = set()
    providers = list(_llm_providers())
    if not providers:
        logger.warning("department categorization: no LLM providers available")
        return result, answered

    for start in range(0, len(values), _LLM_BATCH_SIZE):
        batch = values[start : start + _LLM_BATCH_SIZE]
        user = json.dumps(batch, ensure_ascii=False)
        for name, call in providers:
            try:
                text = call(_LLM_SYSTEM_PROMPT, user)
            except Exception as e:
                logger.warning("department categorization: provider %s errored: %s", name, e)
                text = None
            if not text:
                continue
            parsed = _parse_llm_map(text, batch)
            if parsed:
                # Treat the whole batch as "seen" once one provider gives
                # a usable JSON object for it.
                answered.update(batch)
                result.update(parsed)
                break
    return result, answered


def categorize_values(values, use_llm: bool = True, other_for_unresolved: bool = True) -> dict[str, str]:
    """Full categorization pass over an iterable of distinct raw department
    strings. Rule-based first, then the LLM for the remainder (if
    ``use_llm``).

    ``other_for_unresolved`` controls what happens to a value neither the
    rules nor the LLM place:
      - True  (default): map it to "Other". Use for a final/offline sweep.
      - False: omit it from the result entirely, so the caller leaves the
        row NULL and a later run retries it. Use for the recurring daily
        stage, where "unresolved" is usually just "LLM quota spent today"
        - exactly how skills-matching drains its backlog over days.

    A value the LLM genuinely saw and returned no valid category for is
    always mapped to "Other" regardless of this flag - that's a real
    classification, not a transient miss.
    """
    distinct = sorted({str(v).strip() for v in values if v is not None and str(v).strip()})
    mapping: dict[str, str] = {}
    residual: list[str] = []
    for raw in distinct:
        cat = rule_based_category(raw)
        if cat is not None:
            mapping[raw] = cat
        else:
            residual.append(raw)

    logger.info(
        "department categorization: %d distinct values, %d resolved by rules, %d residual",
        len(distinct), len(mapping), len(residual),
    )

    if residual and use_llm:
        llm_map, answered = llm_categorize(residual)
        mapping.update(llm_map)
        seen_unplaced = [r for r in residual if r in answered and r not in llm_map]
        never_seen = [r for r in residual if r not in answered and r not in llm_map]
        for raw in seen_unplaced:
            mapping[raw] = OTHER
        logger.info(
            "department categorization: LLM placed %d, saw-but-unplaced %d -> Other, unreachable %d",
            len(llm_map), len(seen_unplaced), len(never_seen),
        )
        residual = never_seen
    elif residual and not use_llm:
        # rule-only pass
        pass

    if other_for_unresolved:
        for raw in residual:
            mapping[raw] = OTHER
    return mapping
