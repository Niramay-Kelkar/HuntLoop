"""
Free-form resume-grounded drafting calls for the job-detail chat
assistant's slice 2 (POST /jobs/{job_id}/draft-answer,
huntloop.api.routers.drafting) - "draft a why-this-company answer"
style requests, NOT the batch matched/missing-skills JSON contract that
huntloop.skills_matching* implements.

============================================================================
SECURITY / DEPLOYMENT WARNING - READ BEFORE EXPOSING THIS OUTSIDE LOCALHOST
============================================================================
This module (and the route that calls it) receives a user-supplied,
third-party LLM API key with EVERY request (see draft_with_groq/
draft_with_gemini's api_key argument) and forwards it to that provider.
The key travels from the browser to this API over whatever transport the
API is served on. As of this writing, docker-compose.yml runs this
service over PLAIN HTTP with no TLS termination anywhere in the stack
(see CLAUDE.md - the api/app/db services have no TLS layer at all). A
key sent over plaintext HTTP on a non-localhost network is exposed to
anyone who can observe that traffic.

DO NOT deploy this endpoint on a public/non-localhost host until TLS is
actually in front of the API. This is a real pre-launch blocker, not a
buried caveat - if you are standing up a public deployment, put a TLS
terminator (a reverse proxy, a managed load balancer, etc.) in front of
this service FIRST.
============================================================================

BYOK model, deliberately different from every skills_matching* module:
  - The API key is a PER-CALL ARGUMENT, never read from os.getenv and
    never resolved at import time. This module must import cleanly and
    these functions must work with zero server-side GROQ_API_KEY/
    GEMINI_API_KEY configured anywhere - the whole point of "bring your
    own key" is that the project holds none of its own for this feature.
  - One provider per call, no failover. huntloop.skills_matching_router
    exists to route around the PROJECT's own quota scarcity across its
    own accounts - an unrelated problem to a visitor bringing one key of
    their own. This module does not import skills_matching_router or any
    skills_matching_* module, and callers (huntloop.api.routers.drafting)
    must not build failover logic on top of it either.
  - Free-form text in, free-form text out - no response_format=
    {"type": "json_object"}, no matched/missing-skills parsing. The
    system prompt below is grounding/instruction text, not a JSON-shape
    contract.
  - Own exception types (InvalidApiKey / ProviderRateLimited /
    ProviderCallFailed), NOT huntloop.skills_matching_errors'
    DailyQuotaExhausted/ProviderResponseInvalid - those carry
    batch-failover-across-days semantics ("mark this provider spent for
    the rest of the run") that don't apply to a single interactive
    request from one visitor's own key. A rate-limited BYOK request
    should just fail this one request, not "mark Groq exhausted".

What's genuinely reused vs. net-new, for anyone comparing this to
huntloop.skills_matching_groq_120b / huntloop.skills_matching_gemini:
  - Reused: the `groq` SDK's client-construction shape (`Groq(api_key=...)`),
    Gemini's raw-`requests` REST call shape (same endpoint, same
    `x-goog-api-key` header), and huntloop.text_cleaning.clean_text() for
    cleaning the job description before it goes into the prompt.
  - Net-new: everything else. The batch modules' MODEL_NAME/pacing
    constants, prompts, JSON-parsing, and error classes are tied to the
    batch contract and the project's own keys - none of that is reusable
    for a single free-text prompt/response with a caller-supplied key.
"""
import logging

import requests
from groq import Groq
from groq import AuthenticationError as GroqAuthenticationError
from groq import GroqError, RateLimitError as GroqRateLimitError

from huntloop.text_cleaning import clean_text

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 60

# Picked independently of huntloop.skills_matching_groq_120b's own choice
# of the same model id for the (unrelated) skills-matching batch job -
# this is just a reasonable default model for free-form drafting, not a
# reuse of that module's constant.
GROQ_MODEL = "openai/gpt-oss-120b"
GEMINI_MODEL = "gemini-3.5-flash-lite"

_GEMINI_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)

# Substrings that show up in Gemini's error body when the key itself is
# the problem (as opposed to a real per-request/per-minute rate limit,
# which is a plain 429). Gemini returns 400 (INVALID_ARGUMENT) or 403
# (PERMISSION_DENIED) for a bad key, never a dedicated status code, so
# the body has to be inspected.
_GEMINI_INVALID_KEY_MARKERS = ("api_key_invalid", "api key not valid", "permission_denied")

SUPPORTED_PROVIDERS = ("groq", "gemini")


class InvalidApiKey(Exception):
    """The provider rejected the supplied api_key outright - a bad/expired/
    revoked key, not a quota or transient issue. Maps to HTTP 401 at the
    route layer."""


class ProviderRateLimited(Exception):
    """The provider says this key/account is rate-limited or over quota
    right now. Maps to HTTP 429 at the route layer - distinct from this
    endpoint's own per-IP rate limiter (huntloop.api.routers.drafting),
    which also returns 429; see that module's docstring for why the two
    aren't distinguished in the response shape."""


class ProviderCallFailed(Exception):
    """Any other provider-side failure: unreachable, timed out, malformed
    response, or an HTTP status this module doesn't have a more specific
    mapping for. Maps to HTTP 502 at the route layer."""


def _draft_with_groq(api_key: str, system_prompt: str, user_prompt: str) -> str:
    """One Groq chat-completion call using the CALLER's api_key - a fresh
    client per call, never a shared/cached one, since the key differs
    per request. Returns the drafted text. Raises InvalidApiKey /
    ProviderRateLimited / ProviderCallFailed; never returns None (unlike
    the skills_matching* backends - this is a single interactive request,
    a silent None isn't an option, see this module's docstring)."""
    client = Groq(api_key=api_key)
    try:
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.4,
        )
    except GroqAuthenticationError as e:
        raise InvalidApiKey(str(e)) from e
    except GroqRateLimitError as e:
        raise ProviderRateLimited(str(e)) from e
    except GroqError as e:
        raise ProviderCallFailed(str(e)) from e
    except Exception as e:
        raise ProviderCallFailed(str(e)) from e

    try:
        content = response.choices[0].message.content
    except (IndexError, AttributeError) as e:
        raise ProviderCallFailed(f"Groq response had no usable content: {e}") from e
    if not isinstance(content, str) or not content.strip():
        raise ProviderCallFailed("Groq response content was empty.")
    return content


def _draft_with_gemini(api_key: str, system_prompt: str, user_prompt: str) -> str:
    """One Gemini generateContent call using the CALLER's api_key. Same
    contract as _draft_with_groq - returns text, raises InvalidApiKey /
    ProviderRateLimited / ProviderCallFailed, never returns None."""
    url = _GEMINI_ENDPOINT.format(model=GEMINI_MODEL)
    try:
        resp = requests.post(
            url,
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json={
                "system_instruction": {"parts": [{"text": system_prompt}]},
                "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
                "generationConfig": {"temperature": 0.4},
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as e:
        raise ProviderCallFailed(f"Gemini request failed ({type(e).__name__}): {e}") from e

    if resp.status_code == 429:
        raise ProviderRateLimited(resp.text[:500])
    if resp.status_code in (400, 403):
        body_lower = resp.text.lower()
        if any(m in body_lower for m in _GEMINI_INVALID_KEY_MARKERS):
            raise InvalidApiKey(resp.text[:500])
        raise ProviderCallFailed(f"Gemini returned HTTP {resp.status_code}: {resp.text[:300]!r}")
    if resp.status_code != 200:
        raise ProviderCallFailed(f"Gemini returned HTTP {resp.status_code}: {resp.text[:300]!r}")

    try:
        body = resp.json()
    except ValueError as e:
        raise ProviderCallFailed(f"Gemini response was not valid JSON: {e}") from e

    candidates = body.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ProviderCallFailed(f"Gemini response had no candidates: {body!r}")
    parts = (candidates[0].get("content") or {}).get("parts")
    if not isinstance(parts, list) or not parts:
        finish = candidates[0].get("finishReason")
        raise ProviderCallFailed(f"Gemini candidate had no content parts (finishReason={finish!r})")
    text = parts[0].get("text")
    if not isinstance(text, str) or not text.strip():
        raise ProviderCallFailed("Gemini response text was empty.")
    return text


_DISPATCH = {
    "groq": _draft_with_groq,
    "gemini": _draft_with_gemini,
}


def build_system_prompt(job_title: str, company_name: str, job_description: str | None, resume_text: str) -> str:
    """Assembles the grounding context (job + active resume) that gets
    sent alongside the user's own free-text prompt. Cleaned via
    huntloop.text_cleaning.clean_text() the same way every other
    resume/job-description consumer in this project cleans text before
    handing it to a model - strips HTML/pdfminer artifacts, not tuned
    logic specific to this feature."""
    cleaned_description = clean_text(job_description) if job_description else "(no job description available)"
    cleaned_resume = clean_text(resume_text)
    return (
        "You are helping a job applicant draft a written answer for a specific job application, "
        "grounded in their real resume and the real job posting below. Only draw on what is "
        "actually written in the resume and job description - do not invent experience, employers, "
        "or qualifications the resume doesn't support. Write the answer in the applicant's voice, "
        "as if they wrote it themselves.\n\n"
        "Output format: plain, unformatted text only. This answer will be pasted directly into a "
        "plain-text box on a job application form, which cannot render any formatting. Do not use "
        "any markdown syntax whatsoever - no **bold**, no *italics* or bullet points, no # headers, "
        "no numbered or bulleted lists. Do not restate the question as a title or heading. Do not "
        "add section headers of any kind. Write only natural, flowing paragraphs of prose, exactly "
        "the way a person would type an answer directly into a text box.\n\n"
        f"JOB TITLE: {job_title}\n"
        f"COMPANY: {company_name}\n"
        f"JOB DESCRIPTION:\n{cleaned_description}\n\n"
        f"APPLICANT'S RESUME:\n{cleaned_resume}"
    )


def draft_answer(provider: str, api_key: str, system_prompt: str, user_prompt: str) -> str:
    """Dispatches to the requested provider's drafting call. `provider`
    must be one of SUPPORTED_PROVIDERS - the route layer validates this
    via a Pydantic Literal before it ever reaches here, so an unknown
    value here is a programming error, not a user-facing input to
    handle gracefully."""
    try:
        fn = _DISPATCH[provider]
    except KeyError:
        raise ValueError(f"Unsupported provider: {provider!r}. Supported: {SUPPORTED_PROVIDERS}") from None
    return fn(api_key, system_prompt, user_prompt)
