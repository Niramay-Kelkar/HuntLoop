"""
EXPERIMENTAL - not wired into anything. Matched/missing skills extraction
via Google's Gemini API (free tier), as a candidate SECOND provider
behind the production Groq path (`huntloop.skills_matching`).

Built 2026-08-29 (see SESSIONS.md) to evaluate Gemini as a fallback for
when Groq's real 200,000-tokens-per-day free-tier cap is exhausted mid-
backfill. Same motivation and same "keep it unwired and contract-
identical" approach as `huntloop.skills_matching_ollama`.

The public contract deliberately mirrors `huntloop.skills_matching`
exactly so it can be swapped in by import alone (or dispatched to by the
routing layer designed in huntloop-architecture-decisions.md):

  - match_skills(resume_text, job_description) -> dict | None
  - match_skills_batch(resume_text, job_descriptions) -> list[dict | None]
  - MODEL_NAME, _BATCH_SYSTEM_PROMPT, MAX_PLAUSIBLE_MATCHED_SKILLS
  - DailyQuotaExhausted  (RAISED here too - Gemini's free tier has a real
    per-day request cap, same shape of problem as Groq's TPD cap)

No SDK dependency: talks to the REST endpoint directly with `requests`,
same as the Ollama port. Adding `google-genai` for an unwired experiment
isn't worth it.

  POST https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent

Config:
  - GEMINI_API_KEY   (required at import - fail fast, same as GROQ_API_KEY)
  - GEMINI_MODEL     (default: gemini-2.5-flash-lite - best free-tier RPD)

Failures (rate limit, timeout, connection error, malformed/unparseable
response) log a WARNING and return None / [None] * n, never raise past
this module's boundary - EXCEPT a genuine per-day quota exhaustion, which
raises DailyQuotaExhausted, exactly like the Groq path.
"""
import json
import logging
import os
import time

import requests
from dotenv import load_dotenv

from huntloop.text_cleaning import clean_text

load_dotenv()

logger = logging.getLogger(__name__)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise RuntimeError(
        "GEMINI_API_KEY is not set. Add it to .env "
        "(free tier: https://aistudio.google.com/apikey). This module is "
        "experimental and unwired - only code that explicitly imports "
        "huntloop.skills_matching_gemini needs it."
    )

# gemini-3.5-flash-lite: as of the 2026-08-29 per-account free-tier check
# (see SESSIONS.md), the *-flash-lite 3.x models are the only viable free
# option - 15 RPM / 250K TPM / 500 RPD. The 2.5-generation models have
# been cut to 20 RPD on the free tier (was 1,000), making them useless
# for backfill. gemini-3.1-flash-lite has the same limits and is the
# fallback if 3.5-flash-lite is unavailable.
MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")

_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "{model}:generateContent"
)
REQUEST_TIMEOUT_SECONDS = int(os.getenv("GEMINI_TIMEOUT_SECONDS", "120"))

# Unchanged from the Groq/Ollama era - the failure mode it guards against
# (model dumps the whole resume skills section instead of genuinely
# matching) is model-agnostic. See huntloop.skills_matching for the full
# rationale and the real 53-item anomaly that motivated it.
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
    """Raised by match_skills_batch()/match_skills() when Gemini reports the
    free-tier per-DAY request/token quota is exhausted (HTTP 429 whose
    error payload names a *_per_day / RequestsPerDay quota metric). Same
    contract as huntloop.skills_matching.DailyQuotaExhausted: every other
    failure (per-minute 429, timeout, malformed response) still returns
    None; this one specific, un-recoverable-within-the-day signal is the
    single deliberate exception, so a long backfill run stops cleanly
    instead of retrying for hours."""


# Substrings that distinguish a per-DAY cap from a per-minute cap in
# Gemini's 429 error payloads (QuotaFailure violation messages / metric
# names). Deliberately conservative: a per-minute 429 must NOT be
# misread as daily (that would stop a backfill run for the whole day
# over a transient blip), so only match tokens that unambiguously mean
# "day". "free_tier_requests" alone is NOT here - that metric name is
# used for both the per-minute and per-day free-tier quotas.
_PER_DAY_MARKERS = ("perday", "per_day", "per day", "requestsperday", "requests per day")


def _extract_text(body: dict) -> str | None:
    """Pull the model's text output from a generateContent response, or
    None (logged) if the response is shaped unexpectedly / was blocked."""
    candidates = body.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        logger.warning(f"Gemini response had no candidates: {body!r}")
        return None
    parts = (candidates[0].get("content") or {}).get("parts")
    if not isinstance(parts, list) or not parts:
        finish = candidates[0].get("finishReason")
        logger.warning(f"Gemini candidate had no content parts (finishReason={finish!r})")
        return None
    text = parts[0].get("text")
    if not isinstance(text, str):
        logger.warning(f"Gemini candidate part had no text string: {parts[0]!r}")
        return None
    return text


def _generate(system_prompt: str, user_content: str) -> str | None:
    """One generateContent call forcing JSON output. Returns the raw text
    content, or None on any transport/HTTP failure (logged). Raises
    DailyQuotaExhausted on a per-day 429."""
    url = _ENDPOINT.format(model=MODEL_NAME)
    try:
        resp = requests.post(
            url,
            headers={"x-goog-api-key": GEMINI_API_KEY, "Content-Type": "application/json"},
            json={
                "system_instruction": {"parts": [{"text": system_prompt}]},
                "contents": [{"role": "user", "parts": [{"text": user_content}]}],
                "generationConfig": {
                    "temperature": 0.1,
                    "responseMimeType": "application/json",
                },
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as e:
        logger.warning(f"Gemini call failed ({type(e).__name__}): {e}")
        return None

    if resp.status_code == 429:
        payload = resp.text.lower()
        if any(m in payload for m in _PER_DAY_MARKERS):
            raise DailyQuotaExhausted(resp.text[:500])
        logger.warning(f"Gemini per-minute rate limit (429): {resp.text[:300]!r}")
        return None
    if resp.status_code != 200:
        logger.warning(f"Gemini returned HTTP {resp.status_code}: {resp.text[:300]!r}")
        return None

    try:
        body = resp.json()
    except ValueError as e:
        logger.warning(f"Gemini response was not valid JSON envelope: {e}. Raw: {resp.text[:300]!r}")
        return None

    usage = body.get("usageMetadata") or {}
    if usage:
        logger.debug(
            "Gemini usage: prompt=%s output=%s total=%s",
            usage.get("promptTokenCount"), usage.get("candidatesTokenCount"),
            usage.get("totalTokenCount"),
        )
    return _extract_text(body)


def match_skills(resume_text: str, job_description: str) -> dict | None:
    """Ask Gemini to extract matched/missing skills for this (resume, job)
    pair. Returns {"matched_skills": [...], "missing_skills": [...]} on
    success, or None on any failure. Raises DailyQuotaExhausted on a
    per-day quota 429."""
    cleaned_resume = clean_text(resume_text)
    cleaned_job = clean_text(job_description)

    started = time.monotonic()
    content = _generate(
        _SYSTEM_PROMPT,
        f"RESUME:\n{cleaned_resume}\n\nJOB DESCRIPTION:\n{cleaned_job}",
    )
    elapsed = time.monotonic() - started
    if content is None:
        return None

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as e:
        logger.warning(f"Gemini response was not valid JSON: {e}. Raw content: {content[:300]!r}")
        return None

    matched = parsed.get("matched_skills")
    missing = parsed.get("missing_skills")
    if not isinstance(matched, list) or not isinstance(missing, list):
        logger.warning(
            f"Gemini response JSON missing expected 'matched_skills'/'missing_skills' lists: {parsed!r}"
        )
        return None

    logger.info(f"match_skills: 1 job in {elapsed:.1f}s (model={MODEL_NAME})")
    return {"matched_skills": matched, "missing_skills": missing}


def match_skills_batch(resume_text: str, job_descriptions: list[str]) -> list[dict | None]:
    """Batch version: one resume + N job descriptions in a single Gemini
    call. Batch size is the caller's choice
    (scripts/backfill_skills_matching.py caps at 5, validated on Groq).

    Returns a list the same length as job_descriptions, same order. Each
    entry is {"matched_skills": [...], "missing_skills": [...]} or None if
    that job's result couldn't be recovered (including the whole-batch
    failure case, where every entry is None). Raises DailyQuotaExhausted
    on a per-day quota 429; every other failure returns None."""
    cleaned_resume = clean_text(resume_text)
    cleaned_jobs = [clean_text(jd) for jd in job_descriptions]
    n = len(cleaned_jobs)

    jobs_block = "\n\n".join(f"JOB {i}:\n{jd}" for i, jd in enumerate(cleaned_jobs))
    user_content = f"RESUME:\n{cleaned_resume}\n\n{jobs_block}"

    started = time.monotonic()
    content = _generate(_BATCH_SYSTEM_PROMPT, user_content)
    elapsed = time.monotonic() - started
    if content is None:
        return [None] * n

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as e:
        logger.warning(f"Gemini batch response was not valid JSON: {e}. Raw content: {content[:300]!r}")
        return [None] * n

    results = parsed.get("results")
    if not isinstance(results, list):
        logger.warning(f"Gemini batch response JSON missing a 'results' list: {parsed!r}")
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
            logger.warning(f"Gemini batch response missing/malformed result for job_index={i} (of {n})")
        output.append(by_index.get(i))

    logger.info(f"match_skills_batch: {n} jobs in {elapsed:.1f}s ({elapsed / n:.1f}s/job, model={MODEL_NAME})")
    return output
