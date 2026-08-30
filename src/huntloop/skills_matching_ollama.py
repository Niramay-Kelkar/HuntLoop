"""
EXPERIMENTAL - not wired into anything. Matched/missing skills
extraction via a LOCAL Ollama instance's chat API.

This was built on 2026-08-29 (see SESSIONS.md) to evaluate replacing the
Groq-hosted backend (`huntloop.skills_matching`, `openai/gpt-oss-20b`)
with a local model, motivated by Groq's real 200,000-tokens-per-day
free-tier cap stretching a full ~600-job backfill to 3.4+ days.

VERDICT: NOT adopted. On this dev machine (Intel i5-8257U, 2 cores, 8 GB
RAM, no GPU) every model that fits in RAM (qwen2.5:3b-instruct,
llama3.2:3b) failed the quality bar - full-resume dumps, matched/missing
inversion, prompt-echo, token-repetition spirals - and qwen2.5:7b-instruct
timed out at 900s/job with <1 GB RAM free. Production stays on Groq.
This module is kept intact so the experiment is reproducible on better
hardware (Apple Silicon 16GB+, or a GPU box), where a 7B/14B or
gpt-oss-20b-class local model would be viable. Run
`scripts/validate_ollama_skills_match.py` to re-evaluate.

The public contract deliberately mirrors `huntloop.skills_matching`
exactly (`match_skills`, `match_skills_batch`, `MODEL_NAME`,
`_BATCH_SYSTEM_PROMPT`, `MAX_PLAUSIBLE_MATCHED_SKILLS`,
`DailyQuotaExhausted`) so it could be swapped in by import alone if a
future environment makes local inference viable:

  - `match_skills(resume_text, job_description) -> dict | None`
  - `match_skills_batch(resume_text, job_descriptions) -> list[dict | None]`
  - `MODEL_NAME`, `_BATCH_SYSTEM_PROMPT`, `MAX_PLAUSIBLE_MATCHED_SKILLS`
  - `DailyQuotaExhausted` (kept for import compatibility; a local model
    has no daily quota, so it is never raised anymore - callers that
    catch it still work, the branch is just dead)

Config (both optional, sensible defaults):
  - OLLAMA_HOST  (default http://localhost:11434)
  - OLLAMA_MODEL (default below)

Failures (Ollama not running, connection error, timeout, model not
pulled, malformed/unparseable response) are handled the same defensive
way as before: log a clear WARNING and return None / [None] * n, never
raise past this module's boundary.
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

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")

# Chosen after a real validation pass on this Intel/CPU-only machine
# (2026-08-29, see SESSIONS.md): qwen2.5:7b-instruct was the quality
# ceiling but too slow/RAM-heavy here; qwen2.5:3b-instruct matched the
# Groq baseline on every sample job (including the Duolingo soft-match
# case) at a per-job latency this hardware can actually sustain.
MODEL_NAME = os.getenv("OLLAMA_MODEL", "qwen2.5:3b-instruct")

# Local inference on CPU is slow - a single job can take tens of seconds,
# a batch of 5 a few minutes. Timeout generously; the caller paces itself.
REQUEST_TIMEOUT_SECONDS = int(os.getenv("OLLAMA_TIMEOUT_SECONDS", "600"))

# Context window to request from Ollama. A cleaned resume + a batch of 5
# job descriptions can run to ~6-7k tokens; the model default (often
# 2048/4096) would silently truncate the prompt. Kept explicit.
NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "8192"))

# Hard cap on generated tokens. On CPU-only hardware a model that decides
# to emit a 1500-item JSON list (a real failure mode seen with small
# models - the whole resume dumped verbatim) would run for 5+ minutes
# before the read timeout fires. A genuine matched/missing result is well
# under this; anything that hits the cap was going to be junk anyway.
NUM_PREDICT = int(os.getenv("OLLAMA_NUM_PREDICT", "1500"))

# Sanity ceiling for match_skills_batch()'s per-job matched_skills list -
# see the comment where this is enforced, below. Unchanged from the Groq
# era: the failure mode it guards against (the model dumping the whole
# resume skills section verbatim instead of genuinely matching) is
# model-agnostic.
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
    """Kept only for import compatibility with callers written for the
    Groq backend (scripts/backfill_skills_matching.py catches this). A
    local Ollama model has no tokens-per-day budget, so this is never
    raised anymore - the catch site is harmless dead code until/unless
    the backend changes again."""


def _chat(system_prompt: str, user_content: str) -> str | None:
    """One non-streaming /api/chat call against local Ollama, forcing
    JSON output. Returns the raw response content string, or None on any
    transport/HTTP failure (logged)."""
    try:
        resp = requests.post(
            f"{OLLAMA_HOST}/api/chat",
            json={
                "model": MODEL_NAME,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                "stream": False,
                "format": "json",
                "options": {
                    "temperature": 0.1,
                    "num_ctx": NUM_CTX,
                    "num_predict": NUM_PREDICT,
                },
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as e:
        logger.warning(
            f"Ollama chat call failed ({type(e).__name__}): {e}. "
            f"Is `ollama serve` running at {OLLAMA_HOST} with model {MODEL_NAME!r} pulled?"
        )
        return None

    if resp.status_code != 200:
        logger.warning(
            f"Ollama chat returned HTTP {resp.status_code}: {resp.text[:300]!r} "
            f"(model={MODEL_NAME!r})"
        )
        return None

    try:
        body = resp.json()
    except ValueError as e:
        logger.warning(f"Ollama chat response was not valid JSON envelope: {e}. Raw: {resp.text[:300]!r}")
        return None

    content = (body.get("message") or {}).get("content")
    if not isinstance(content, str):
        logger.warning(f"Ollama chat response had no message.content string: {body!r}")
        return None

    # eval_count / eval_duration (ns) are Ollama's own token/timing stats -
    # cheap, useful signal for the slow-CPU case, logged at debug level.
    ec, ed = body.get("eval_count"), body.get("eval_duration")
    if ec and ed:
        logger.debug(f"Ollama: {ec} completion tokens in {ed / 1e9:.1f}s ({ec / (ed / 1e9):.1f} tok/s)")

    return content


def match_skills(resume_text: str, job_description: str) -> dict | None:
    """Ask the local model to extract matched/missing skills for this
    (resume, job) pair. Returns {"matched_skills": [...],
    "missing_skills": [...]} on success, or None on any failure (Ollama
    down, timeout, connection error, or a response that isn't the
    expected JSON shape) - the caller decides what "no result" means for
    its own run, this function just never raises past its own boundary."""
    cleaned_resume = clean_text(resume_text)
    cleaned_job = clean_text(job_description)

    started = time.monotonic()
    content = _chat(
        _SYSTEM_PROMPT,
        f"RESUME:\n{cleaned_resume}\n\nJOB DESCRIPTION:\n{cleaned_job}",
    )
    elapsed = time.monotonic() - started
    if content is None:
        return None

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as e:
        logger.warning(f"Ollama response was not valid JSON: {e}. Raw content: {content[:300]!r}")
        return None

    matched = parsed.get("matched_skills")
    missing = parsed.get("missing_skills")
    if not isinstance(matched, list) or not isinstance(missing, list):
        logger.warning(
            f"Ollama response JSON missing expected 'matched_skills'/'missing_skills' lists: {parsed!r}"
        )
        return None

    logger.info(f"match_skills: 1 job in {elapsed:.1f}s (model={MODEL_NAME})")
    return {"matched_skills": matched, "missing_skills": missing}


def match_skills_batch(resume_text: str, job_descriptions: list[str]) -> list[dict | None]:
    """Batch version of match_skills(): one resume + N job descriptions in
    a single model call. The batch size is chosen by the caller
    (scripts/backfill_skills_matching.py caps it at 5, the size quality
    was validated at on the Groq backend and re-checked on Ollama).

    Returns a list the same length as job_descriptions, in the same
    order. Each entry is either {"matched_skills": [...],
    "missing_skills": [...]} or None if that job's result couldn't be
    recovered from the response - including the whole-batch-failed case
    (Ollama error, unparseable JSON), where every entry is None, since a
    single failed call can't be attributed to one job within the batch.

    Never raises. (DailyQuotaExhausted is no longer raised - see its
    docstring - but is still exported for callers that catch it.)"""
    cleaned_resume = clean_text(resume_text)
    cleaned_jobs = [clean_text(jd) for jd in job_descriptions]
    n = len(cleaned_jobs)

    jobs_block = "\n\n".join(f"JOB {i}:\n{jd}" for i, jd in enumerate(cleaned_jobs))
    user_content = f"RESUME:\n{cleaned_resume}\n\n{jobs_block}"

    started = time.monotonic()
    content = _chat(_BATCH_SYSTEM_PROMPT, user_content)
    elapsed = time.monotonic() - started
    if content is None:
        return [None] * n

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as e:
        logger.warning(f"Ollama batch response was not valid JSON: {e}. Raw content: {content[:300]!r}")
        return [None] * n

    results = parsed.get("results")
    if not isinstance(results, list):
        logger.warning(f"Ollama batch response JSON missing a 'results' list: {parsed!r}")
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

        # Sanity filter (unchanged from the Groq era): reject a result
        # whose matched_skills looks like the whole resume skills section
        # dumped verbatim rather than genuine matching. matched_skills is
        # inherently bounded - a resume only genuinely overlaps a single
        # job on a limited set of specific skills - unlike missing_skills,
        # which can legitimately run to 20-30+ items for a badly-matched
        # job. Leave such a job's result NULL so it gets reprocessed
        # rather than storing suspect data.
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
            logger.warning(f"Ollama batch response missing/malformed result for job_index={i} (of {n})")
        output.append(by_index.get(i))

    logger.info(f"match_skills_batch: {n} jobs in {elapsed:.1f}s ({elapsed / n:.1f}s/job, model={MODEL_NAME})")
    return output
