"""
Matched/missing skills extraction via Mistral's La Plateforme API
("Experiment" free tier) - the THIRD skills-matching provider, behind the
existing Groq (primary) -> Gemini (fallback) chain in
`huntloop.skills_matching_router`.

Built 2026-09-03 from the candidate research in
huntloop-architecture-decisions.md ("Backup (3rd) skills-matching
provider candidate list", 2026-08-30). Mistral was the recommended first
choice there for headroom, native JSON mode, 128K context (no forced
batch-size cut), and a first-party OpenAI-compatible endpoint - the same
integration shape as Groq/Gemini.

WIRING STATUS: wired-but-NOT-recommended, opt-in only.
`SKILLS_MATCHING_PROVIDERS` still defaults to "groq,gemini". The 11-job
side-by-side validation against the Groq baseline (2026-09-03, see
SESSIONS.md + huntloop-architecture-decisions.md) came back POOR: the
only free-usable model (ministral-8b - the flagship mistral-small/-medium
/-large are req-limited to 0 on the free tier now) full-resume-dumps into
matched_skills on 7 of 11 jobs, inverts the résumé-grounding rule, and
ignores the short-phrase format rule. Kept wired as a pure last-resort
capacity bucket (better than a stalled backlog if Groq AND Gemini are
ever both walled), not as a quality peer of the other two.

The public contract deliberately mirrors `huntloop.skills_matching` and
`huntloop.skills_matching_gemini` exactly so the router can dispatch to
any of them interchangeably:

  - match_skills(resume_text, job_description) -> dict | None
  - match_skills_batch(resume_text, job_descriptions) -> list[dict | None]
  - MODEL_NAME, _SYSTEM_PROMPT, _BATCH_SYSTEM_PROMPT, MAX_PLAUSIBLE_MATCHED_SKILLS
  - MAX_BATCH_SIZE / MAX_BATCH_ESTIMATED_TOKENS / TARGET_TPM / MAX_RPM
    (this provider's own pacing limits - see below; NOT Groq's/Gemini's)
  - DailyQuotaExhausted  (the shared class from skills_matching_errors;
    Mistral's free tier has no per-*day* cap, but its ~1B tokens/*month*
    ceiling is the same shape of problem - "this bucket is spent for a
    long window, stop retrying it")

No SDK dependency: talks to the REST endpoint directly with `requests`,
same as the Gemini backend. `mistralai` for one more backend isn't worth
the dependency.

  POST https://api.mistral.ai/v1/chat/completions   (OpenAI-compatible)

Config:
  - MISTRAL_API_KEY   (required at import - fail fast, same as GROQ_API_KEY)
  - MISTRAL_MODEL      (default: ministral-8b-latest)

IMPORTANT re-verification finding (2026-09-03): Mistral's free tier is now
gated PER MODEL. The flagship `mistral-small-latest` / `-medium` / `-large`
models return HTTP 429 with `x-ratelimit-limit-req-minute: 0` on this
workspace's free tier - i.e. NOT usable free at all (the same kind of
quiet cut Gemini made to its 2.5-gen models). The `ministral-*` small
models ARE free-usable: live headers show `ministral-8b-latest` at
625,000 tokens/min and 188 req/min (both higher than the 500K/1-RPS the
2026-08-30 research recorded), no per-day header. `ministral-8b-latest`
is the default here for that reason - `ministral-3b-latest` also works
but 8B is the better pick for a nuanced extraction task.

Failures (rate limit, timeout, connection error, malformed/unparseable
response) log a WARNING and return None / [None] * n, never raise past
this module's boundary - EXCEPT a genuine monthly-quota exhaustion, which
raises DailyQuotaExhausted, exactly like the Groq/Gemini paths.
"""
import json
import logging
import os
import time

import requests
from dotenv import load_dotenv

from huntloop.skills_matching_errors import DailyQuotaExhausted
from huntloop.text_cleaning import clean_text

load_dotenv()

logger = logging.getLogger(__name__)

MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY")
if not MISTRAL_API_KEY:
    raise RuntimeError(
        "MISTRAL_API_KEY is not set. Add it to .env "
        "(free 'Experiment' tier: https://admin.mistral.ai/organization/api-keys). "
        "Only code that puts 'mistral' in SKILLS_MATCHING_PROVIDERS and imports "
        "huntloop.skills_matching_mistral needs it - the default groq,gemini "
        "chain never loads this module."
    )

# ministral-8b-latest: the flagship mistral-small/-medium/-large models
# are 429 / req-limit-0 on the free tier as of 2026-09-03 (see the module
# docstring); ministral-8b is free-usable, has a 128K context (no
# per-request truncation risk on the wider ATS set's ~9.5k-char JDs), and
# is the largest of the free ministral models. Override via MISTRAL_MODEL.
MODEL_NAME = os.getenv("MISTRAL_MODEL", "ministral-8b-latest")

_ENDPOINT = "https://api.mistral.ai/v1/chat/completions"
REQUEST_TIMEOUT_SECONDS = int(os.getenv("MISTRAL_TIMEOUT_SECONDS", "120"))

# Unchanged from the Groq/Gemini backends - the failure mode it guards
# against (model dumps the whole resume skills section instead of genuinely
# matching) is model-agnostic. See huntloop.skills_matching for the full
# rationale and the real 53-item anomaly that motivated it. The 2026-08-30
# research flagged Mistral's JSON-schema enforcement as "historically a bit
# looser than OpenAI/Gemini", so this backstop matters here.
MAX_PLAUSIBLE_MATCHED_SKILLS = 20

_SYSTEM_PROMPT = (
    "You are a resume-to-job skills matcher. Given a candidate's resume text and a job "
    "description, identify the specific skills/technologies/qualifications the job asks for. "
    "Respond with ONLY a JSON object of this exact shape, no other text: "
    '{"matched_skills": [list of skills the job wants that the resume genuinely demonstrates], '
    '"missing_skills": [list of skills the job wants that the resume does not show]}. '
    "Keep each skill as a short phrase (e.g. \"Python\", \"AWS\", \"B2B marketing campaigns\"). "
    "CRITICAL - grounding rule for matched_skills: a skill belongs in matched_skills ONLY if "
    "the RESUME ITSELF contains specific evidence for it - the skill named outright in the "
    "resume, or a project, role, or accomplishment described in the resume that clearly "
    "demonstrates it. A skill that appears only in the job description does NOT qualify. "
    "'The job posting mentions X' is not the same as 'the resume shows X' - only the second "
    "earns a place in matched_skills. Before adding any skill to matched_skills, confirm you "
    "could point to the exact resume text that evidences it; if you cannot, put it in "
    "missing_skills instead. Do not guess, infer, or assume skills that aren't written in the "
    "resume, however plausible they seem for this candidate."
)

_BATCH_SYSTEM_PROMPT = (
    "You are a resume-to-job skills matcher. Given a candidate resume and a numbered list of "
    "job descriptions, identify for EACH job the specific skills/technologies/qualifications it "
    "asks for. Respond with ONLY a JSON object of this exact shape, no other text: "
    '{"results": [{"job_index": int, "matched_skills": [...], "missing_skills": [...]}, ...]}. '
    "Include exactly one entry per job index, in the same order given. Keep each skill as a short "
    "phrase (e.g. \"Python\", \"AWS\", \"B2B marketing campaigns\"). "
    "CRITICAL - grounding rule for matched_skills: for EVERY job, a skill belongs in that job's "
    "matched_skills ONLY if the RESUME ITSELF contains specific evidence for it - the skill named "
    "outright in the resume, or a project, role, or accomplishment described in the resume that "
    "clearly demonstrates it. A skill that appears only in a job description does NOT qualify. "
    "'The job posting mentions X' is not the same as 'the resume shows X' - only the second earns "
    "a place in matched_skills. Before adding any skill to a job's matched_skills, confirm you "
    "could point to the exact resume text that evidences it; if you cannot, put it in that job's "
    "missing_skills instead. Do not guess, infer, or assume skills that aren't written in the "
    "resume, however plausible they seem for this candidate."
)


# --- Batch-sizing / pacing limits for THIS provider ---
# Read from the REAL live rate-limit response headers for
# ministral-8b-latest on this workspace's free tier, 2026-09-03 (NOT the
# 2026-08-30 research's 500K TPM / 1 RPS figures - those describe the now
# req-limit-0 flagship models; the ministral models are less restricted):
#   x-ratelimit-limit-tokens-minute: 625,000
#   x-ratelimit-limit-req-minute:    188
#   (no per-day / per-month header surfaced)
#
#   - MAX_BATCH_ESTIMATED_TOKENS: Mistral's 128K context means there is NO
#     Groq-style per-request hard cap, so this is set generously - well
#     above a batch of 5 long JDs (~15k tokens) so the batch always fills
#     to MAX_BATCH_SIZE rather than being token-clipped. This is the
#     "no forced batch-size cut" property the research picked Mistral for.
#   - MAX_BATCH_SIZE = 5: the size batch quality was validated at (on
#     Groq). Larger-batch quality is unverified on any provider, so we
#     don't push past it even though Mistral's capacity would allow it.
#   - TARGET_TPM = 500,000: paced under the real 625,000 TPM (margin for
#     the header's token-cost being a pre-call estimate; same spirit as
#     Groq 6k-under-8k / Gemini 200k-under-250k).
#   - MAX_RPM = 120: comfortably under the real 188 req/min. Never binds
#     in practice - each batch is a multi-second LLM round trip processed
#     strictly sequentially by backfill_skills_matching.py, so the natural
#     rate is far lower; it's a backstop like Groq's non-binding MAX_RPM=30.
#
# Effective capacity at these limits is far above HuntLoop's ~120 relevant
# jobs/day - still the highest-headroom of the three providers.
MAX_BATCH_SIZE = 5
MAX_BATCH_ESTIMATED_TOKENS = 30_000
TARGET_TPM = 500_000
MAX_RPM = 120


# Substrings that distinguish Mistral's ~1B-tokens-per-MONTH ceiling (a
# long-window "this bucket is spent" state, mapped to DailyQuotaExhausted
# so the router marks the provider down for the run) from an ordinary
# per-second / per-minute 429 (transient - return None, retried next run).
# Deliberately conservative: a transient 429 must NOT be misread as the
# monthly wall (that would drop the provider for the whole run over a
# blip), so only match phrasings that unambiguously mean the long-window
# usage cap. In practice this is expected to never fire - the monthly
# ceiling is ~30x the workload's monthly token use.
_MONTHLY_QUOTA_MARKERS = (
    "per month", "per-month", "permonth", "tokens per month",
    "monthly limit", "monthly quota", "usage limit exceeded",
    "monthly usage", "exceeded your monthly",
)


def _extract_text(body: dict) -> str | None:
    """Pull the model's text output from an OpenAI-compatible
    chat/completions response, or None (logged) if the response is shaped
    unexpectedly / was truncated or filtered."""
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        logger.warning(f"Mistral response had no choices: {body!r}")
        return None
    first = choices[0] if isinstance(choices[0], dict) else {}
    message = first.get("message") or {}
    content = message.get("content")
    if not isinstance(content, str) or not content:
        finish = first.get("finish_reason")
        logger.warning(f"Mistral choice had no content string (finish_reason={finish!r})")
        return None
    return content


def _generate(system_prompt: str, user_content: str) -> str | None:
    """One chat/completions call forcing JSON output. Returns the raw text
    content, or None on any transport/HTTP failure (logged). Raises
    DailyQuotaExhausted on a monthly-quota 429."""
    try:
        resp = requests.post(
            _ENDPOINT,
            headers={
                "Authorization": f"Bearer {MISTRAL_API_KEY}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json={
                "model": MODEL_NAME,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                "temperature": 0.1,
                "response_format": {"type": "json_object"},
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as e:
        logger.warning(f"Mistral call failed ({type(e).__name__}): {e}")
        return None

    if resp.status_code == 429:
        payload = resp.text.lower()
        if any(m in payload for m in _MONTHLY_QUOTA_MARKERS):
            raise DailyQuotaExhausted(resp.text[:500])
        logger.warning(f"Mistral rate limit (429): {resp.text[:300]!r}")
        return None
    if resp.status_code != 200:
        logger.warning(f"Mistral returned HTTP {resp.status_code}: {resp.text[:300]!r}")
        return None

    try:
        body = resp.json()
    except ValueError as e:
        logger.warning(f"Mistral response was not a valid JSON envelope: {e}. Raw: {resp.text[:300]!r}")
        return None

    usage = body.get("usage") or {}
    if usage:
        logger.debug(
            "Mistral usage: prompt=%s completion=%s total=%s",
            usage.get("prompt_tokens"), usage.get("completion_tokens"), usage.get("total_tokens"),
        )
    return _extract_text(body)


def match_skills(resume_text: str, job_description: str) -> dict | None:
    """Ask Mistral to extract matched/missing skills for this (resume, job)
    pair. Returns {"matched_skills": [...], "missing_skills": [...]} on
    success, or None on any failure. Raises DailyQuotaExhausted on a
    monthly-quota 429."""
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
        logger.warning(f"Mistral response was not valid JSON: {e}. Raw content: {content[:300]!r}")
        return None

    matched = parsed.get("matched_skills")
    missing = parsed.get("missing_skills")
    if not isinstance(matched, list) or not isinstance(missing, list):
        logger.warning(
            f"Mistral response JSON missing expected 'matched_skills'/'missing_skills' lists: {parsed!r}"
        )
        return None

    logger.info(f"match_skills: 1 job in {elapsed:.1f}s (model={MODEL_NAME})")
    return {"matched_skills": matched, "missing_skills": missing}


def match_skills_batch(resume_text: str, job_descriptions: list[str]) -> list[dict | None]:
    """Batch version: one resume + N job descriptions in a single Mistral
    call. Batch size is the caller's choice
    (scripts/backfill_skills_matching.py caps at 5, validated on Groq).

    Returns a list the same length as job_descriptions, same order. Each
    entry is {"matched_skills": [...], "missing_skills": [...]} or None if
    that job's result couldn't be recovered (including the whole-batch
    failure case, where every entry is None). Raises DailyQuotaExhausted
    on a monthly-quota 429; every other failure returns None."""
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
        logger.warning(f"Mistral batch response was not valid JSON: {e}. Raw content: {content[:300]!r}")
        return [None] * n

    results = parsed.get("results")
    if not isinstance(results, list):
        logger.warning(f"Mistral batch response JSON missing a 'results' list: {parsed!r}")
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
            logger.warning(f"Mistral batch response missing/malformed result for job_index={i} (of {n})")
        output.append(by_index.get(i))

    logger.info(f"match_skills_batch: {n} jobs in {elapsed:.1f}s ({elapsed / n:.1f}s/job, model={MODEL_NAME})")
    return output
