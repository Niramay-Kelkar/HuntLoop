"""
The Resume Deep-Dive generator: one heavier LLM call that produces a
structured skill-gap analysis of the active resume against ONE job
posting (summary, fit rating, strengths, gaps, resume edits, interview
focus, caveats).

WHERE THIS RUNS - the important part. Unlike every other LLM-calling
module in this project (huntloop.skills_matching*,
huntloop.resume_ats_report, huntloop.feedback_triage), this one is NOT
called from the API process. It runs inside the Modal container defined
in huntloop.modal_resume_functions.deep_dive_remote, which reads
GROQ_API_KEY/GEMINI_API_KEY from the Modal Secret named
"huntloop-llm". That is the whole point of the feature's shape: the
public demo API has no LLM provider keys at all (render.yaml
deliberately defines none - see CLAUDE.md), so the keys only ever exist
inside Modal's own secret store and its container environment, never in
the API's environment and never in a request body (unlike the BYOK
huntloop.drafting path).

Consequences for how this module is written, all deliberate:

  * NO import-time key check. Every other LLM module here raises
    RuntimeError at import if GROQ_API_KEY is unset (the fail-fast
    convention). This one must not: huntloop.api.routers.deep_dive
    imports validate_deep_dive_result() from here to validate a result
    coming BACK from Modal, in a process that legitimately has no keys.
    Keys are read at call time, inside generate_deep_dive() only.
  * NO module-level third-party imports. `groq` and `requests` are
    imported lazily inside their own provider functions, and `dotenv` is
    not imported at all (the remote container has no python-dotenv - see
    huntloop.modal_resume_functions' docstring for the crash-loop this
    exact class of import caused the first time around). Module level
    here is stdlib plus huntloop.text_cleaning, which is itself
    stdlib-only (html + re).

Public surface:

    generate_deep_dive(resume_text, job_title, company_name,
                       job_description, matched_skills, missing_skills)
        -> dict                      (raises AllDeepDiveProvidersFailed)

    validate_deep_dive_result(obj) -> dict | None
    RESULT_VERSION
    AllDeepDiveProvidersFailed

Provider chain and failover: the same order the rest of the project
uses (huntloop.skills_matching_router's default chain) - Groq
openai/gpt-oss-120b, then Groq openai/gpt-oss-20b, then Gemini. A
provider that errors, returns non-JSON, or returns JSON that fails
validate_deep_dive_result() fails over to the next one, exactly like
huntloop.resume_ats_report's Groq->Gemini fallthrough but with three
stages. If every provider fails, this raises
AllDeepDiveProvidersFailed - a DISTINCT error, not a None return,
because unlike the batch skills-matching path there is no "leave it
NULL and pick it up next run" option here: a Deep-Dive is a single
user-initiated run whose row has to end up `failed` with a real reason.

Prompt discipline is carried over from the skills-matching prompts
rather than reinvented, in particular the resume-grounding rule added
to the Gemini prompts in 2026-08-30 after real ungrounded matches were
found in production: "the job posting mentions X" is not evidence that
"the resume shows X". Here that rule matters even more than it does for
a flat skills list, since every `strengths` entry has to cite its own
resume evidence.

Nothing here logs or returns a key value, and nothing logs resume or
job-description text - provider failures are logged by type/status
only, never with the prompt or the raw body (the raw body can echo
prompt content back). That is stricter than huntloop.resume_ats_report,
which logs a truncated raw response on a parse failure; this module
deliberately does not.
"""
import json
import logging
import os

from huntloop.text_cleaning import clean_text

logger = logging.getLogger(__name__)

# Stamped into every stored result so a future prompt/shape change can
# be told apart from an older cached row without guessing. Bump this
# when the output shape changes in a way the frontend has to care about.
RESULT_VERSION = "deep-dive-v1"

GROQ_PRIMARY_MODEL = "openai/gpt-oss-120b"
GROQ_SECONDARY_MODEL = "openai/gpt-oss-20b"
GEMINI_MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")

# Defensive input ceilings - mirrors huntloop.resume_ats_report's
# _MAX_RESUME_TEXT_CHARS and huntloop.feedback_triage's own guard. Real
# pdfplumber-extracted resumes are a few thousand characters and real
# job descriptions average ~9.5k (measured - see CLAUDE.md's note on
# Groq batch collapse at that size), so these are headroom, not a
# real-world cap.
MAX_RESUME_TEXT_CHARS = 20_000
MAX_JOB_DESCRIPTION_CHARS = 20_000
MAX_SKILLS_PER_LIST = 40

# Output caps, applied by validate_deep_dive_result() to whatever the
# model actually returned. A model that ignores the prompt's own "at
# most N" instructions must not be able to make this row arbitrarily
# large - the result is stored as JSON in Postgres and rendered
# verbatim in the browser.
MAX_SUMMARY_CHARS = 1_200
MAX_LIST_ITEMS = 6
MAX_CAVEAT_ITEMS = 4
MAX_SHORT_STRING_CHARS = 240
MAX_LONG_STRING_CHARS = 600

FIT_RATINGS = ("strong", "moderate", "stretch")
IMPORTANCE_LEVELS = ("high", "medium", "low")


class AllDeepDiveProvidersFailed(Exception):
    """Every provider in the chain failed (error, non-JSON response, or
    a response that failed shape validation). Raised by
    generate_deep_dive() and allowed to propagate out of the Modal
    function so the caller's run is recorded as `failed` with a real
    reason, rather than silently storing a placeholder result."""


_SYSTEM_PROMPT = (
    "You are a careful technical recruiter doing a deep skill-gap analysis of ONE candidate "
    "against ONE job posting. You are given the candidate's resume text, the job title, the "
    "company name, the job description, and a precomputed list of skills already assessed as "
    "matched and missing. "
    "Respond with ONLY a JSON object, no markdown and no other text, in exactly this shape: "
    '{"summary": "<2-3 sentences on how this candidate stacks up against this specific role>", '
    '"fit_rating": "<exactly one of: strong, moderate, stretch>", '
    '"strengths": [{"point": "<short phrase naming the strength>", '
    '"resume_evidence": "<the specific thing in the RESUME that demonstrates it>"}], '
    '"gaps": [{"skill": "<short phrase>", "importance": "<exactly one of: high, medium, low>", '
    '"why_it_matters": "<why this job in particular needs it>", '
    '"how_to_close": "<a concrete, actionable step>"}], '
    '"resume_edits": [{"section": "<which resume section to change>", '
    '"suggestion": "<the concrete edit to make>"}], '
    '"interview_focus": ["<short string naming something to prepare>"], '
    '"caveats": ["<short string stating a real limitation of this analysis>"]}. '
    "Return at most 5 strengths, 5 gaps, 5 resume_edits, 5 interview_focus items and 3 caveats. "
    "CRITICAL - grounding rule: a strength belongs in `strengths` ONLY if the RESUME ITSELF "
    "shows it. 'The job posting mentions X' is not the same as 'the resume shows X' - only the "
    "second earns a strengths slot, and `resume_evidence` must point at something actually "
    "present in the resume text, never at the job description and never invented. If you cannot "
    "cite real resume evidence for a point, leave it out of `strengths` and consider it a gap "
    "instead. "
    "Base everything only on what is actually written in the two texts - do not guess at the "
    "candidate's seniority, employer prestige, visa status, or anything else not stated. "
    "Keep every string concrete and specific to THIS resume and THIS posting, never generic "
    "filler. Put honest limitations in `caveats` (for example: the resume text was extracted "
    "from a PDF and may be incomplete, or the posting is vague about a requirement)."
)

_REQUIRED_KEYS = (
    "summary",
    "fit_rating",
    "strengths",
    "gaps",
    "resume_edits",
    "interview_focus",
    "caveats",
)


# ---------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------


def _skill_list(values, label: str) -> str:
    if not isinstance(values, list):
        return f"{label}: (not available)"
    items = [str(v).strip() for v in values[:MAX_SKILLS_PER_LIST] if str(v).strip()]
    if not items:
        return f"{label}: (none recorded)"
    return f"{label}: {', '.join(items)}"


def build_user_content(
    resume_text: str,
    job_title: str,
    company_name: str,
    job_description: str,
    matched_skills,
    missing_skills,
) -> str:
    """The user-message half of the prompt. Separate from
    generate_deep_dive() so it can be unit-tested without any provider
    call, the same way huntloop.drafting.build_system_prompt() is."""
    cleaned_resume = clean_text(resume_text or "")[:MAX_RESUME_TEXT_CHARS]
    cleaned_job = clean_text(job_description or "")[:MAX_JOB_DESCRIPTION_CHARS]
    return (
        f"JOB TITLE: {job_title or '(not given)'}\n"
        f"COMPANY: {company_name or '(not given)'}\n\n"
        f"{_skill_list(matched_skills, 'ALREADY-ASSESSED MATCHED SKILLS')}\n"
        f"{_skill_list(missing_skills, 'ALREADY-ASSESSED MISSING SKILLS')}\n\n"
        f"RESUME:\n{cleaned_resume}\n\n"
        f"JOB DESCRIPTION:\n{cleaned_job}"
    )


# ---------------------------------------------------------------------
# Output validation / capping
# ---------------------------------------------------------------------


def _clean_string(value, max_chars: int) -> str | None:
    if not isinstance(value, (str, int, float)):
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:max_chars]


def _string_list(value, max_items: int, max_chars: int) -> list[str]:
    if not isinstance(value, list):
        return []
    out = []
    for item in value[:max_items]:
        cleaned = _clean_string(item, max_chars)
        if cleaned is not None:
            out.append(cleaned)
    return out


def _object_list(value, fields: dict[str, int], enums: dict[str, tuple] | None = None) -> list[dict]:
    """Coerces a list of dicts, keeping only entries where EVERY named
    field is present and usable (and, for a field named in `enums`, is
    one of the allowed values - case-insensitively, since models
    routinely return "High" for an enum documented as "high"). `fields`
    maps field name -> max characters."""
    if not isinstance(value, list):
        return []
    enums = enums or {}
    out = []
    for entry in value[:MAX_LIST_ITEMS]:
        if not isinstance(entry, dict):
            continue
        coerced = {}
        ok = True
        for field, max_chars in fields.items():
            cleaned = _clean_string(entry.get(field), max_chars)
            if cleaned is None:
                ok = False
                break
            if field in enums:
                lowered = cleaned.lower()
                if lowered not in enums[field]:
                    ok = False
                    break
                cleaned = lowered
            coerced[field] = cleaned
        if ok:
            out.append(coerced)
    return out


def validate_deep_dive_result(obj) -> dict | None:
    """Confirms a parsed Deep-Dive payload has every required key in a
    usable shape, and caps every list length and string length (see the
    MAX_* constants). Returns the normalized dict, or None - never
    raises - on anything malformed.

    Used in TWO places on purpose, which is why it lives here rather
    than inside either caller:
      1. generate_deep_dive() below, to decide whether a provider's
         response is usable or should fail over to the next provider;
      2. huntloop.api.routers.deep_dive, to re-validate the result
         handed back by modal.FunctionCall.get() before copying it into
         resume_deep_dives.result. That second check is not redundant:
         the API process did not produce that JSON and must not trust
         its shape just because a Modal call reported success.

    A result missing `summary` or `fit_rating` is rejected outright
    (those two carry the whole headline of the feature). The five list
    sections are allowed to come back empty - a genuinely strong
    candidate can have no gaps worth naming - so an empty list is
    normalized to [] rather than treated as a validation failure.
    """
    if not isinstance(obj, dict) or any(key not in obj for key in _REQUIRED_KEYS):
        return None

    summary = _clean_string(obj.get("summary"), MAX_SUMMARY_CHARS)
    if summary is None:
        return None

    fit_rating = _clean_string(obj.get("fit_rating"), 32)
    if fit_rating is None or fit_rating.lower() not in FIT_RATINGS:
        return None
    fit_rating = fit_rating.lower()

    return {
        "version": RESULT_VERSION,
        "summary": summary,
        "fit_rating": fit_rating,
        "strengths": _object_list(
            obj.get("strengths"),
            {"point": MAX_SHORT_STRING_CHARS, "resume_evidence": MAX_LONG_STRING_CHARS},
        ),
        "gaps": _object_list(
            obj.get("gaps"),
            {
                "skill": MAX_SHORT_STRING_CHARS,
                "importance": 32,
                "why_it_matters": MAX_LONG_STRING_CHARS,
                "how_to_close": MAX_LONG_STRING_CHARS,
            },
            enums={"importance": IMPORTANCE_LEVELS},
        ),
        "resume_edits": _object_list(
            obj.get("resume_edits"),
            {"section": MAX_SHORT_STRING_CHARS, "suggestion": MAX_LONG_STRING_CHARS},
        ),
        "interview_focus": _string_list(
            obj.get("interview_focus"), MAX_LIST_ITEMS, MAX_SHORT_STRING_CHARS
        ),
        "caveats": _string_list(obj.get("caveats"), MAX_CAVEAT_ITEMS, MAX_SHORT_STRING_CHARS),
    }


def _parse_and_validate(raw_text: str, provider: str) -> dict | None:
    """json.loads + validate, logging only the failure KIND - never the
    raw body, which can echo resume/job text straight back."""
    if not raw_text or not raw_text.strip():
        logger.warning("Deep-Dive: %s returned an empty response body", provider)
        return None
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as e:
        logger.warning("Deep-Dive: %s returned invalid JSON (%s)", provider, e.msg)
        return None
    result = validate_deep_dive_result(parsed)
    if result is None:
        logger.warning("Deep-Dive: %s returned JSON that failed shape validation", provider)
    return result


# ---------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------


def _via_groq(model: str, user_content: str) -> dict | None:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        logger.info("Deep-Dive: no GROQ_API_KEY in this environment - skipping %s", model)
        return None

    from groq import Groq, GroqError  # lazy - see module docstring

    try:
        response = Groq(api_key=api_key).chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
        )
    except GroqError as e:
        logger.warning("Deep-Dive: Groq %s call failed (%s)", model, type(e).__name__)
        return None
    except Exception as e:
        logger.warning("Deep-Dive: unexpected error calling Groq %s (%s)", model, type(e).__name__)
        return None

    return _parse_and_validate(response.choices[0].message.content, f"Groq {model}")


def _via_gemini(user_content: str) -> dict | None:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        logger.info("Deep-Dive: no GEMINI_API_KEY in this environment - skipping Gemini")
        return None

    import requests  # lazy - see module docstring

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL_NAME}:generateContent"
    )
    try:
        resp = requests.post(
            url,
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json={
                "system_instruction": {"parts": [{"text": _SYSTEM_PROMPT}]},
                "contents": [{"role": "user", "parts": [{"text": user_content}]}],
                "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
            },
            timeout=120,
        )
    except requests.RequestException as e:
        logger.warning("Deep-Dive: Gemini call failed (%s)", type(e).__name__)
        return None

    if resp.status_code != 200:
        # Status code only - never resp.text, which echoes the prompt
        # back on some error shapes.
        logger.warning("Deep-Dive: Gemini returned HTTP %s", resp.status_code)
        return None

    try:
        text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
    except (ValueError, KeyError, IndexError, TypeError):
        logger.warning("Deep-Dive: Gemini response envelope had an unexpected shape")
        return None

    return _parse_and_validate(text, "Gemini")


def generate_deep_dive(
    resume_text: str,
    job_title: str,
    company_name: str,
    job_description: str,
    matched_skills=None,
    missing_skills=None,
) -> dict:
    """Groq 120b, then Groq 20b, then Gemini - the project's standard
    chain order. Returns the validated result dict (always carrying
    `version`); raises AllDeepDiveProvidersFailed if every provider in
    the chain failed, was unconfigured, or returned something that
    didn't validate."""
    user_content = build_user_content(
        resume_text=resume_text,
        job_title=job_title,
        company_name=company_name,
        job_description=job_description,
        matched_skills=matched_skills,
        missing_skills=missing_skills,
    )

    attempts = (
        ("groq_120b", lambda: _via_groq(GROQ_PRIMARY_MODEL, user_content)),
        ("groq", lambda: _via_groq(GROQ_SECONDARY_MODEL, user_content)),
        ("gemini", lambda: _via_gemini(user_content)),
    )

    for name, call in attempts:
        result = call()
        if result is not None:
            logger.info("Deep-Dive generated via %s", name)
            return result
        logger.info("Deep-Dive: %s did not produce a usable result - trying the next provider", name)

    raise AllDeepDiveProvidersFailed(
        "Every configured provider (Groq gpt-oss-120b, Groq gpt-oss-20b, Gemini) failed to "
        "produce a usable Deep-Dive result."
    )
