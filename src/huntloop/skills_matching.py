"""
Matched/missing skills extraction for a (resume, job description) pair,
via Groq's free-tier chat completions API.

GROQ_API_KEY is read from .env the same way DATABASE_URL is in
huntloop.settings: read once at import time, fail fast (RuntimeError) if
missing, never silently proceed. This module isn't imported by
settings.py/main.py/pipelines.py, so environments that don't use this
feature (the core scraper, most of the test suite) never pay that cost -
only code that actually imports huntloop.skills_matching needs
GROQ_API_KEY set.

Model: `openai/gpt-oss-20b`, confirmed live against Groq's own
`/v1/models` endpoint before picking it (2026-08-22, see SESSIONS.md) -
Llama 3.x chat models (the obvious first guess) are not currently active
on Groq's free tier at all; gpt-oss-20b is a real, active, general-
purpose instruction-following model there instead, and was verified
end-to-end with a real request to return valid structured JSON via
`response_format={"type": "json_object"}` before being adopted here.

Failures (rate limit, timeout, malformed/unparseable response) are
handled the same defensive way as detect_ats()'s Playwright fallback and
huntloop.metrics' Pushgateway push: log a clear warning and return None,
never raise - a Groq outage must not crash whatever's calling this.
"""
import json
import logging
import os

from dotenv import load_dotenv
from groq import Groq, GroqError

from huntloop.text_cleaning import clean_text

load_dotenv()

logger = logging.getLogger(__name__)

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
if not GROQ_API_KEY:
    raise RuntimeError(
        "GROQ_API_KEY is not set. Copy .env.example to .env and fill in your Groq API key "
        "(free tier: https://console.groq.com/keys)."
    )

MODEL_NAME = "openai/gpt-oss-20b"

# Sanity ceiling for match_skills_batch()'s per-job matched_skills list -
# see the comment where this is enforced, below.
MAX_PLAUSIBLE_MATCHED_SKILLS = 20

_SYSTEM_PROMPT = (
    "You are a resume-to-job skills matcher. Given a candidate's resume text and a job "
    "description, identify the specific skills/technologies/qualifications the job asks for. "
    "Respond with ONLY a JSON object of this exact shape, no other text: "
    '{"matched_skills": [list of skills the job wants that the resume genuinely demonstrates], '
    '"missing_skills": [list of skills the job wants that the resume does not show]}. '
    "Keep each skill as a short phrase (e.g. \"Python\", \"AWS\", \"B2B marketing campaigns\"). "
    "Base this only on what's actually written in both texts - do not guess or assume skills "
    "that aren't stated."
)

_client = None


def _get_client() -> Groq:
    global _client
    if _client is None:
        _client = Groq(api_key=GROQ_API_KEY)
    return _client


def match_skills(resume_text: str, job_description: str) -> dict | None:
    """Ask Groq to extract matched/missing skills for this (resume, job)
    pair. Returns {"matched_skills": [...], "missing_skills": [...]} on
    success, or None on any failure (rate limit, timeout, connection
    error, or a response that isn't the expected JSON shape) - the
    caller decides what "no result" means for its own run, this function
    just never raises past its own boundary."""
    cleaned_resume = clean_text(resume_text)
    cleaned_job = clean_text(job_description)

    try:
        response = _get_client().chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"RESUME:\n{cleaned_resume}\n\nJOB DESCRIPTION:\n{cleaned_job}",
                },
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
        )
    except GroqError as e:
        logger.warning(f"Groq API call failed ({type(e).__name__}): {e}")
        return None
    except Exception as e:
        logger.warning(f"Unexpected error calling Groq API: {e}", exc_info=True)
        return None

    content = response.choices[0].message.content

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as e:
        logger.warning(f"Groq response was not valid JSON: {e}. Raw content: {content[:300]!r}")
        return None

    matched = parsed.get("matched_skills")
    missing = parsed.get("missing_skills")
    if not isinstance(matched, list) or not isinstance(missing, list):
        logger.warning(
            f"Groq response JSON missing expected 'matched_skills'/'missing_skills' lists: {parsed!r}"
        )
        return None

    return {"matched_skills": matched, "missing_skills": missing}


_BATCH_SYSTEM_PROMPT = (
    "You are a resume-to-job skills matcher. Given a candidate resume and a numbered list of "
    "job descriptions, identify for EACH job the specific skills/technologies/qualifications it "
    "asks for. Respond with ONLY a JSON object of this exact shape, no other text: "
    '{"results": [{"job_index": int, "matched_skills": [...], "missing_skills": [...]}, ...]}. '
    "Include exactly one entry per job index, in the same order given. Keep each skill as a short "
    "phrase (e.g. \"Python\", \"AWS\", \"B2B marketing campaigns\"). Base this only on what's "
    "actually written in the resume and each job description - do not guess or assume skills "
    "that aren't stated."
)


class DailyQuotaExhausted(Exception):
    """Raised by match_skills_batch() specifically when Groq reports the
    account's tokens-per-day (TPD) budget is exhausted - a distinct,
    real limit from the per-minute (TPM) one, discovered only by running
    a real ~600-call backfill (2026-08-22, see SESSIONS.md): a fresh
    per-minute pacer has no way to know this budget is gone until it
    hits it, and once it's gone, retrying every ~60s for hours is a
    waste, not resilience - a caller running a multi-hundred-call
    backfill should stop the whole run on this specific signal rather
    than let match_skills_batch()'s normal "return None and keep going"
    contract mask it. All other failure modes (per-minute rate limit,
    timeout, malformed response, etc.) still return None as usual -
    this is the one deliberate exception to that contract, and only for
    this one specific, otherwise-unrecoverable-within-the-day signal."""


def match_skills_batch(resume_text: str, job_descriptions: list[str]) -> list[dict | None]:
    """Batch version of match_skills(): one resume + N job descriptions in
    a single Groq call. Measured ~2.3x more token-efficient per job than
    N separate match_skills() calls, with no quality cost, at N=5 (see
    SESSIONS.md, 2026-08-22) - N=10 was rejected outright by Groq's
    8000-token hard per-request cap, so callers must keep batches small;
    5 is the validated size, not an arbitrary default.

    Returns a list the same length as job_descriptions, in the same
    order. Each entry is either {"matched_skills": [...],
    "missing_skills": [...]} or None if that job's result couldn't be
    recovered from the response - including the whole-batch-failed case
    (API error, unparseable JSON), where every entry is None, since a
    single failed call can't be attributed to one job within the batch.
    Raises DailyQuotaExhausted (not caught here) specifically when Groq
    reports the daily token budget is gone - see that class's docstring.
    Every other failure mode still returns None, never raises."""
    cleaned_resume = clean_text(resume_text)
    cleaned_jobs = [clean_text(jd) for jd in job_descriptions]
    n = len(cleaned_jobs)

    jobs_block = "\n\n".join(f"JOB {i}:\n{jd}" for i, jd in enumerate(cleaned_jobs))
    user_content = f"RESUME:\n{cleaned_resume}\n\n{jobs_block}"

    try:
        response = _get_client().chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": _BATCH_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
        )
    except GroqError as e:
        if "tokens per day" in str(e).lower():
            raise DailyQuotaExhausted(str(e)) from e
        logger.warning(f"Groq batch API call failed ({type(e).__name__}) for {n} jobs: {e}")
        return [None] * n
    except Exception as e:
        logger.warning(f"Unexpected error calling Groq batch API for {n} jobs: {e}", exc_info=True)
        return [None] * n

    content = response.choices[0].message.content
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as e:
        logger.warning(f"Groq batch response was not valid JSON: {e}. Raw content: {content[:300]!r}")
        return [None] * n

    results = parsed.get("results")
    if not isinstance(results, list):
        logger.warning(f"Groq batch response JSON missing a 'results' list: {parsed!r}")
        return [None] * n

    by_index = {}
    for item in results:
        if not isinstance(item, dict):
            continue
        idx = item.get("job_index")
        matched = item.get("matched_skills")
        missing = item.get("missing_skills")
        if not (isinstance(idx, int) and isinstance(matched, list) and isinstance(missing, list)):
            continue

        # Sanity filter: a real backfill run (2026-08-22, see SESSIONS.md)
        # produced one result (Palantir "Deployment Strategist") with 53
        # matched_skills - effectively the entire resume's skills section
        # dumped verbatim, not genuine matching (sibling items in the
        # same batch call got short, sensible, distinct results, so this
        # was the model's own failure on that one item, not a parsing
        # bug). matched_skills is inherently bounded - a resume only
        # genuinely overlaps a single job on a limited set of specific
        # skills - unlike missing_skills, which can legitimately run to
        # 20-30+ items for a badly-matched job (real, correct examples
        # already seen: Checkr "Chief of Staff", Duolingo "Creative
        # Director, Marketing"). MAX_PLAUSIBLE_MATCHED_SKILLS is set well
        # above every legitimate matched_skills count observed so far
        # (0-10 across ~76 real results) and well below the one known
        # anomaly (53) - wide margin on both sides to avoid rejecting a
        # real, unusually skills-heavy match while still reliably
        # catching this failure mode.
        if len(matched) > MAX_PLAUSIBLE_MATCHED_SKILLS:
            logger.warning(
                f"Rejecting result for job_index={idx} (of {n}): matched_skills has "
                f"{len(matched)} items (> {MAX_PLAUSIBLE_MATCHED_SKILLS}), which looks like a "
                f"full-resume dump rather than genuine matching - leaving this job's result "
                f"NULL so it gets reprocessed rather than storing suspect data. "
                f"First few items: {matched[:5]!r}"
            )
            continue

        by_index[idx] = {"matched_skills": matched, "missing_skills": missing}

    output = []
    for i in range(n):
        if i not in by_index:
            logger.warning(f"Groq batch response missing/malformed result for job_index={i} (of {n})")
        output.append(by_index.get(i))
    return output
