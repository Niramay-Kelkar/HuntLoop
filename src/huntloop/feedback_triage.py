"""
One-line LLM summary/classification of a feedback report, for
scripts/triage_feedback.py.

Reuses the same two providers (and the same GROQ_API_KEY/GEMINI_API_KEY
env vars) as huntloop.skills_matching_router, with the same Groq-primary/
Gemini-fallback failover shape - but this is a genuinely different task
(summarize one short free-text report, not batch-match a resume against
job descriptions), so it is its own small module with its own prompt
rather than a new mode bolted onto skills_matching_router's batch
contract.

Public surface:

    summarize_feedback(category, raw_text) -> str | None

Failures (rate limit, timeout, malformed response) on one provider fall
through to the next; if every configured provider fails, returns None
and the caller (scripts/triage_feedback.py) leaves that row's
`triage_status` as `pending` for a later run - same "never crash the
caller, just report no result" contract every other LLM-calling module
in this project uses (huntloop.skills_matching, huntloop.drafting).

GROQ_API_KEY is required (same fail-fast-at-import-time convention as
huntloop.skills_matching); GEMINI_API_KEY is optional - if unset, the
Gemini fallback is simply skipped rather than erroring.
"""
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

# A real submission is capped at 2000 chars at the API layer (see
# huntloop.api.routers.feedback) - this is just a defensive ceiling for
# anything that reaches this module directly (e.g. a future caller).
_MAX_RAW_TEXT_CHARS = 4000

_SYSTEM_PROMPT = (
    "You triage user-submitted feedback reports for a job-search web app. Given a report's "
    "category and raw text, write ONE short sentence (plain text, no markdown, under 160 "
    "characters) that summarizes what the report is actually about, in a way useful for a "
    "human reviewer scanning a list of reports. Be specific and concrete - name the feature, "
    "page, or behavior the report mentions, if any. Do not add commentary, advice, or "
    "a priority judgement. Respond with ONLY the one-sentence summary, no quotes, no prefix."
)

_client = None


def _get_groq_client() -> Groq:
    global _client
    if _client is None:
        _client = Groq(api_key=GROQ_API_KEY)
    return _client


def _user_content(category: str, raw_text: str) -> str:
    return f"CATEGORY: {category}\n\nREPORT TEXT:\n{raw_text[:_MAX_RAW_TEXT_CHARS]}"


def _via_groq(category: str, raw_text: str) -> str | None:
    try:
        response = _get_groq_client().chat.completions.create(
            model=GROQ_MODEL_NAME,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": _user_content(category, raw_text)},
            ],
            temperature=0.2,
        )
    except GroqError as e:
        logger.warning(f"Groq feedback-triage call failed ({type(e).__name__}): {e}")
        return None
    except Exception as e:
        logger.warning(f"Unexpected error calling Groq for feedback triage: {e}", exc_info=True)
        return None

    content = response.choices[0].message.content
    if not content or not content.strip():
        logger.warning("Groq feedback-triage response had empty content")
        return None
    return content.strip()


def _via_gemini(category: str, raw_text: str) -> str | None:
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
                "contents": [{"role": "user", "parts": [{"text": _user_content(category, raw_text)}]}],
                "generationConfig": {"temperature": 0.2},
            },
            timeout=60,
        )
    except requests.RequestException as e:
        logger.warning(f"Gemini feedback-triage call failed ({type(e).__name__}): {e}")
        return None

    if resp.status_code != 200:
        logger.warning(f"Gemini feedback-triage call returned HTTP {resp.status_code}: {resp.text[:300]!r}")
        return None

    try:
        body = resp.json()
        text = body["candidates"][0]["content"]["parts"][0]["text"]
    except (ValueError, KeyError, IndexError, TypeError) as e:
        logger.warning(f"Gemini feedback-triage response had an unexpected shape ({e}): {resp.text[:300]!r}")
        return None

    text = text.strip()
    return text or None


def summarize_feedback(category: str, raw_text: str) -> str | None:
    """Groq first, Gemini fallback. Returns a one-line summary string, or
    None if every configured provider failed - the caller leaves the row
    pending for a later run rather than storing a placeholder."""
    summary = _via_groq(category, raw_text)
    if summary is not None:
        return summary
    logger.info("Groq failed for feedback triage - trying Gemini fallback")
    return _via_gemini(category, raw_text)
