"""
One LLM call that scores a resume's ATS (applicant-tracking-system)
compatibility and gives concrete, structured feedback - for
huntloop.api.routers.resumes's GET /resumes/active/ats-report.

Reuses the same two providers (and the same GROQ_API_KEY/GEMINI_API_KEY
env vars) as huntloop.skills_matching_router / huntloop.feedback_triage,
with the same Groq-primary/Gemini-fallback failover shape - but this is
a genuinely different task (score + critique ONE resume on its own,
no job description involved at all) from either of those, so it is its
own small module with its own prompt rather than a new mode bolted onto
match_skills_batch's resume-vs-job-description contract.

Public surface:

    generate_ats_report(resume_text) -> dict | None
        {"score": int (0-100),
         "keyword_feedback": list[str],
         "wording_feedback": list[str],
         "formatting_feedback": list[str]}

Unlike huntloop.feedback_triage's one-line-string contract, this asks
for structured JSON (response_format={"type": "json_object"}, the same
mechanism huntloop.skills_matching_groq_120b already uses) so the
frontend can render a score and three labeled feedback lists without
any text parsing on this end.

Failures (rate limit, timeout, malformed/unparseable/wrong-shaped
response) on one provider fall through to the next; if every configured
provider fails, returns None and the caller
(huntloop.api.routers.resumes) surfaces a 502 and does NOT cache
anything - same "never crash the caller, just report no result"
contract every other LLM-calling module in this project uses.

GROQ_API_KEY is required (same fail-fast-at-import-time convention as
huntloop.skills_matching/huntloop.feedback_triage); GEMINI_API_KEY is
optional - if unset, the Gemini fallback is simply skipped.
"""
import json
import logging
import os

from dotenv import load_dotenv
from groq import Groq, GroqError

load_dotenv()

logger = logging.getLogger(__name__)

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
if not GROQ_API_KEY:
    raise RuntimeError(
        "GROQ_API_KEY is not set. Copy .env.example to .env and fill in your Groq API key."
    )
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

GROQ_MODEL_NAME = "openai/gpt-oss-20b"
GEMINI_MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")

# Defensive ceiling on how much resume text this module will ever send -
# mirrors huntloop.feedback_triage's _MAX_RAW_TEXT_CHARS guard. Real
# resumes extracted via pdfplumber are a few thousand characters; this
# is generous headroom, not a real-world cap.
_MAX_RESUME_TEXT_CHARS = 20_000

_SYSTEM_PROMPT = (
    "You are an ATS (applicant tracking system) resume reviewer for a job-search web app. "
    "Given a candidate's resume text, assess how well it would survive an automated ATS "
    "parser and keyword scan - NOT how good a fit the candidate is for any specific job "
    "(no job description is provided; do not invent one). "
    "Respond with ONLY a JSON object, no markdown, in exactly this shape: "
    '{"score": <integer 0-100, overall ATS compatibility>, '
    '"keyword_feedback": [<1-4 short strings about keyword coverage/strength - skills, '
    "tools, or role-relevant terms that are present, weak, or missing in a general sense>], "
    '"wording_feedback": [<1-4 short strings about wording quality - weak verbs, vague '
    "phrasing, missing quantified impact, passive voice>], "
    '"formatting_feedback": [<1-4 short strings about formatting/parseability - tables, '
    "columns, headers, special characters, or layout choices that could confuse an ATS "
    "parser, or a note that the format looks clean if so>]}. "
    "Each feedback string must be concrete and specific to THIS resume's actual content, "
    "never generic filler. If a category has nothing notable, return a single string saying "
    "so rather than an empty list."
)

_REQUIRED_KEYS = ("score", "keyword_feedback", "wording_feedback", "formatting_feedback")

_client = None


def _get_groq_client() -> Groq:
    global _client
    if _client is None:
        _client = Groq(api_key=GROQ_API_KEY)
    return _client


def _user_content(resume_text: str) -> str:
    return f"RESUME TEXT:\n{resume_text[:_MAX_RESUME_TEXT_CHARS]}"


def _coerce_feedback_list(value) -> list[str] | None:
    if not isinstance(value, list) or not value:
        return None
    items = [str(item).strip() for item in value if str(item).strip()]
    return items or None


def _validate_shape(parsed) -> dict | None:
    """Confirms the parsed JSON has every required key in a usable
    shape, coercing feedback lists to list[str]. Returns None (never
    raises) on anything malformed - the caller treats that exactly like
    a provider error and falls through to the next provider."""
    if not isinstance(parsed, dict) or any(key not in parsed for key in _REQUIRED_KEYS):
        return None
    try:
        score = int(parsed["score"])
    except (TypeError, ValueError):
        return None
    if not 0 <= score <= 100:
        return None

    keyword_feedback = _coerce_feedback_list(parsed["keyword_feedback"])
    wording_feedback = _coerce_feedback_list(parsed["wording_feedback"])
    formatting_feedback = _coerce_feedback_list(parsed["formatting_feedback"])
    if keyword_feedback is None or wording_feedback is None or formatting_feedback is None:
        return None

    return {
        "score": score,
        "keyword_feedback": keyword_feedback,
        "wording_feedback": wording_feedback,
        "formatting_feedback": formatting_feedback,
    }


def _via_groq(resume_text: str) -> dict | None:
    try:
        response = _get_groq_client().chat.completions.create(
            model=GROQ_MODEL_NAME,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": _user_content(resume_text)},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
        )
    except GroqError as e:
        logger.warning(f"Groq ATS-report call failed ({type(e).__name__}): {e}")
        return None
    except Exception as e:
        logger.warning(f"Unexpected error calling Groq for ATS report: {e}", exc_info=True)
        return None

    content = response.choices[0].message.content
    if not content or not content.strip():
        logger.warning("Groq ATS-report response had empty content")
        return None
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as e:
        logger.warning(f"Groq ATS-report response was not valid JSON: {e}. Raw content: {content[:300]!r}")
        return None

    result = _validate_shape(parsed)
    if result is None:
        logger.warning(f"Groq ATS-report response had an unexpected shape: {content[:300]!r}")
    return result


def _via_gemini(resume_text: str) -> dict | None:
    if not GEMINI_API_KEY:
        return None
    import requests

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL_NAME}:generateContent"
    )
    try:
        resp = requests.post(
            url,
            headers={"x-goog-api-key": GEMINI_API_KEY, "Content-Type": "application/json"},
            json={
                "system_instruction": {"parts": [{"text": _SYSTEM_PROMPT}]},
                "contents": [{"role": "user", "parts": [{"text": _user_content(resume_text)}]}],
                "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
            },
            timeout=60,
        )
    except requests.RequestException as e:
        logger.warning(f"Gemini ATS-report call failed ({type(e).__name__}): {e}")
        return None

    if resp.status_code != 200:
        logger.warning(f"Gemini ATS-report call returned HTTP {resp.status_code}: {resp.text[:300]!r}")
        return None

    try:
        body = resp.json()
        text = body["candidates"][0]["content"]["parts"][0]["text"]
    except (ValueError, KeyError, IndexError, TypeError) as e:
        logger.warning(f"Gemini ATS-report response had an unexpected shape ({e}): {resp.text[:300]!r}")
        return None

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        logger.warning(f"Gemini ATS-report response was not valid JSON: {e}. Raw text: {text[:300]!r}")
        return None

    result = _validate_shape(parsed)
    if result is None:
        logger.warning(f"Gemini ATS-report response had an unexpected shape: {text[:300]!r}")
    return result


def generate_ats_report(resume_text: str) -> dict | None:
    """Groq first, Gemini fallback. Returns the structured report dict,
    or None if every configured provider failed or returned something
    unusable - the caller surfaces an error and does NOT cache anything
    rather than storing a placeholder."""
    report = _via_groq(resume_text)
    if report is not None:
        return report
    logger.info("Groq failed for ATS report - trying Gemini fallback")
    return _via_gemini(resume_text)
