"""
Matched/missing skills extraction via Groq's free-tier chat completions
API, targeting `openai/gpt-oss-120b` instead of the production `openai/
gpt-oss-20b` (`huntloop.skills_matching`) - a same-provider, bigger-model
capacity stage, added 2026-09-04 (see SESSIONS.md +
huntloop-architecture-decisions.md).

Why a separate module instead of parameterizing `huntloop.skills_matching`
with a model argument: every other backend in this project
(skills_matching / skills_matching_gemini / skills_matching_mistral) is
one module per provider with its own module-level MODEL_NAME/pacing
constants, and `huntloop.skills_matching_router` dispatches by provider
NAME to a hardcoded module import - not by module+parameter. A separate
module keeps this addition contract-identical to every other backend and
needs zero router-shape changes beyond adding one more name.

CONFIRMED LIVE (2026-09-04, real API calls, not assumed - see SESSIONS.md
for the full header capture): `openai/gpt-oss-120b` has its OWN
independent rate-limit bucket from `openai/gpt-oss-20b` - hammering one
model's requests does not touch the other's `x-ratelimit-remaining-*`
headers. Both models show IDENTICAL live limits: 1,000 requests/day
(RPD) and 8,000 tokens/minute (TPM). Groq's response headers expose
exactly one pair for "requests" and one pair for "tokens" - no separate
RPM or TPD header was observed for either model. This means a second
Groq model on this SAME account/key is a real, independent capacity
addition (confirmed, not assumed) - not a shared-pool illusion.

The public contract mirrors `huntloop.skills_matching` /
`huntloop.skills_matching_gemini` / `huntloop.skills_matching_mistral`
exactly so the router can dispatch to any of them interchangeably:

  - match_skills(resume_text, job_description) -> dict | None
  - match_skills_batch(resume_text, job_descriptions) -> list[dict | None]
  - MODEL_NAME, _SYSTEM_PROMPT, _BATCH_SYSTEM_PROMPT, MAX_PLAUSIBLE_MATCHED_SKILLS
  - MAX_BATCH_SIZE / MAX_BATCH_ESTIMATED_TOKENS / TARGET_TPM / MAX_RPM
  - DailyQuotaExhausted / ProviderResponseInvalid (shared classes from
    skills_matching_errors - same Groq SDK, same failure shapes)

Uses the official `groq` SDK, same as `huntloop.skills_matching` - no new
dependency. GROQ_API_KEY is the SAME key already required for the primary
20b backend (this is a same-account capacity stage, not a new provider),
so it fails fast on the same env var, not a separate one.
"""
import json
import logging
import os

from dotenv import load_dotenv
from groq import Groq, GroqError

from huntloop.skills_matching_errors import DailyQuotaExhausted, ProviderResponseInvalid
from huntloop.text_cleaning import clean_text

load_dotenv()

logger = logging.getLogger(__name__)

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
if not GROQ_API_KEY:
    raise RuntimeError(
        "GROQ_API_KEY is not set. This is the same key huntloop.skills_matching "
        "(the openai/gpt-oss-20b backend) already requires - copy .env.example to "
        ".env and fill in your Groq API key (free tier: https://console.groq.com/keys)."
    )

MODEL_NAME = "openai/gpt-oss-120b"

# Unchanged from every other backend - the failure mode it guards against
# (model dumps the whole resume skills section instead of genuinely
# matching) is model-agnostic. See huntloop.skills_matching for the full
# rationale.
MAX_PLAUSIBLE_MATCHED_SKILLS = 20

# --- Batch-sizing / pacing limits for THIS provider/model ---
# Live-confirmed 2026-09-04 (real header capture, repeated calls - see
# SESSIONS.md): openai/gpt-oss-120b shows x-ratelimit-limit-requests=1000
# (an RPD bucket - its reset time grows by ~86.4s per request consumed,
# i.e. 86400s/1000 = a request refilling once per 86.4s, unambiguously a
# 1,000/day cadence, not a per-minute one) and
# x-ratelimit-limit-tokens=8000 (a TPM bucket - sub-second/low-second
# resets, refilling within the same call-to-call gap). These are
# IDENTICAL to openai/gpt-oss-20b's live numbers and to each other's
# independent buckets (see the module docstring) - so the same
# margin-below-the-cap reasoning huntloop.skills_matching already uses
# applies unchanged here:
#   - MAX_BATCH_ESTIMATED_TOKENS = 7,000: mirrors the 20b backend's value,
#     which is set below Groq's real ~8,000-token HARD per-request cap
#     (a batch of 10 real jobs 413'd there - see huntloop.skills_matching
#     and huntloop-architecture-decisions.md). That hard cap was NOT
#     independently re-verified for 120b specifically (doing so would mean
#     deliberately sending an oversized request and eating the failure) -
#     it is inherited on the assumption that Groq's per-request payload
#     cap is a platform-level constraint, not a per-model one specific to
#     20b, until real evidence says otherwise.
#   - MAX_BATCH_SIZE = 5: the size batch quality was validated at (on the
#     20b backend). Kept identical rather than pushed higher.
#   - TARGET_TPM = 6,000: paced under the live-confirmed 8,000 TPM, same
#     margin the 20b backend already uses.
#   - MAX_RPM = 30: no per-minute request header was ever observed for
#     either model (only the RPD-shaped counter) - this value is inherited
#     from the 20b backend as a non-binding backstop, not itself
#     live-confirmed. In practice it never binds: at MAX_BATCH_ESTIMATED_
#     TOKENS=7,000 against an 8,000 TPM budget, the token pacer alone
#     already holds batch cadence to roughly one batch per minute, far
#     under any plausible RPM ceiling.
MAX_BATCH_SIZE = 5
MAX_BATCH_ESTIMATED_TOKENS = 7_000
TARGET_TPM = 6_000
MAX_RPM = 30

# Substrings in a Groq 400 body meaning "the JSON-mode validator rejected
# the model's own output" - identical to the 20b backend's marker list;
# this is a property of Groq's JSON-mode validator layer, not the model.
_JSON_VALIDATE_FAILED_MARKERS = ("json_validate_failed", "failed to validate json")

# Substrings meaning "a long-window (day-scale) quota is exhausted".
# "tokens per day" is the PROVEN marker (huntloop.skills_matching hit
# this for real in production against openai/gpt-oss-20b's TPD wall - see
# SESSIONS.md 2026-08-22). "requests per day" is added defensively, NOT
# from an observed live message: live headers here confirm the daily
# ceiling for this model is REQUEST-shaped (RPD=1,000), not token-shaped,
# and deliberately exhausting 1,000 real requests just to capture the
# exact 429 wording was not done (it would burn a full day's quota on a
# validation task). If Groq's real RPD-exhaustion message doesn't contain
# either substring, that failure falls through to the generic
# `return [None] * n` path instead of raising DailyQuotaExhausted - a
# known, documented gap, not a silently assumed fix.
_DAILY_QUOTA_MARKERS = ("tokens per day", "requests per day")

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

_client = None


def _get_client() -> Groq:
    global _client
    if _client is None:
        _client = Groq(api_key=GROQ_API_KEY)
    return _client


def match_skills(resume_text: str, job_description: str) -> dict | None:
    """Ask openai/gpt-oss-120b to extract matched/missing skills for this
    (resume, job) pair. Returns {"matched_skills": [...], "missing_skills":
    [...]} on success, or None on any failure - never raises past its own
    boundary except DailyQuotaExhausted (see _DAILY_QUOTA_MARKERS)."""
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
        msg = str(e).lower()
        if any(m in msg for m in _DAILY_QUOTA_MARKERS):
            raise DailyQuotaExhausted(str(e)) from e
        logger.warning(f"Groq (120b) API call failed ({type(e).__name__}): {e}")
        return None
    except Exception as e:
        logger.warning(f"Unexpected error calling Groq (120b) API: {e}", exc_info=True)
        return None

    content = response.choices[0].message.content

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as e:
        logger.warning(f"Groq (120b) response was not valid JSON: {e}. Raw content: {content[:300]!r}")
        return None

    matched = parsed.get("matched_skills")
    missing = parsed.get("missing_skills")
    if not isinstance(matched, list) or not isinstance(missing, list):
        logger.warning(
            f"Groq (120b) response JSON missing expected 'matched_skills'/'missing_skills' lists: {parsed!r}"
        )
        return None

    return {"matched_skills": matched, "missing_skills": missing}


def match_skills_batch(resume_text: str, job_descriptions: list[str]) -> list[dict | None]:
    """Batch version: one resume + N job descriptions in a single
    openai/gpt-oss-120b call. Same contract as every other backend's
    match_skills_batch(). Raises DailyQuotaExhausted (see
    _DAILY_QUOTA_MARKERS) and ProviderResponseInvalid
    (json_validate_failed) as router failover signals; every other
    failure returns [None] * n."""
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
        msg = str(e).lower()
        if any(m in msg for m in _DAILY_QUOTA_MARKERS):
            raise DailyQuotaExhausted(str(e)) from e
        if any(m in msg for m in _JSON_VALIDATE_FAILED_MARKERS):
            raise ProviderResponseInvalid(str(e)) from e
        logger.warning(f"Groq (120b) batch API call failed ({type(e).__name__}) for {n} jobs: {e}")
        return [None] * n
    except Exception as e:
        logger.warning(f"Unexpected error calling Groq (120b) batch API for {n} jobs: {e}", exc_info=True)
        return [None] * n

    content = response.choices[0].message.content
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as e:
        logger.warning(f"Groq (120b) batch response was not valid JSON: {e}. Raw content: {content[:300]!r}")
        return [None] * n

    results = parsed.get("results")
    if not isinstance(results, list):
        logger.warning(f"Groq (120b) batch response JSON missing a 'results' list: {parsed!r}")
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

        # Same sanity filter as every other backend - see
        # huntloop.skills_matching for the full rationale.
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
            logger.warning(f"Groq (120b) batch response missing/malformed result for job_index={i} (of {n})")
        output.append(by_index.get(i))
    return output
