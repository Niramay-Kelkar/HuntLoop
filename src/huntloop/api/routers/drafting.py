"""
POST /jobs/{job_id}/draft-answer - slice 2 of the job-detail chat
assistant: resume-grounded application-answer drafting via a
user-supplied ("bring your own key") LLM API key.

A separate module from huntloop.api.routers.jobs on purpose: this is a
genuinely different concern (an outbound call to a third-party LLM
provider, using a caller-supplied credential) with its own failure
modes (bad key, provider rate limit, provider outage) that no other
/jobs endpoint has to handle.

============================================================================
SECURITY / DEPLOYMENT WARNING - see huntloop.drafting's module docstring
for the full statement. Short version: this endpoint forwards a
user-supplied third-party API key over whatever transport this API is
served on. The stack (docker-compose.yml) currently runs plain HTTP with
no TLS anywhere. DO NOT expose this endpoint on a public/non-localhost
deployment until TLS is actually in front of this service.
============================================================================

BYOK contract (see huntloop.drafting for the provider-call layer):
  - The frontend sends job_id (path) + prompt + provider + api_key. It
    does NOT send the job description or resume text - both are looked
    up server-side (see _lookup_job_and_active_resume below, which reuses
    the exact "SELECT ... WHERE is_active = true" active-resume pattern
    huntloop.api.routers.jobs._active_resume_embedding() already uses,
    just for extracted_text instead of embedding).
  - Exactly one provider per request, no failover - this module never
    imports huntloop.skills_matching_router or any skills_matching_*
    module. A request that fails just fails; the caller can retry with a
    different provider/key if they want.
  - No fallback to any server-side GROQ_API_KEY/GEMINI_API_KEY env var.
    A request with no api_key is a 400, full stop - see
    _require_api_key() below.

Per-IP rate limiting: a bare in-process sliding-window counter (see
_RateLimiter below), not a new dependency (no slowapi/redis in
requirements.txt, and a single-process dev/local deployment doesn't need
one). This bounds server compute/bandwidth exposure on an unauthenticated
endpoint - it is NOT protecting any shared LLM quota (the user's own key
means this project bears no LLM cost from this endpoint at all).
Known limitation, stated plainly: this counter is per-process memory -
it resets on restart and does not share state across multiple uvicorn/
gunicorn workers. Fine for today's single-process deployment; would need
a shared store (Redis, etc.) behind multiple workers.

Distinguishing this endpoint's own 429 from a provider's 429: NOT done in
the response *shape* - both come back as a plain FastAPI HTTPException
with a `detail` string, no extra field naming which kind of 429 it was.
They ARE distinguishable by reading `detail` (this endpoint's own limiter
says "Rate limit exceeded for this endpoint..."; a provider rate limit's
detail is provider-specific, from huntloop.drafting.ProviderRateLimited).
Flagged explicitly per the task: a machine client can't distinguish the
two by status code alone, only by parsing `detail` text - a real, if
minor, gap; a future revision could add a `source` field
("rate_limited_by": "huntloop" | "provider") if a caller ever needs to
branch on it programmatically.
"""
import logging
import threading
import time
from collections import defaultdict, deque

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from huntloop.api.dependencies import get_db
from huntloop.api.schemas.drafting import DraftAnswerRequest, DraftAnswerResponse
from huntloop.db_models import Company, JobPosting, ResumeVersion
from huntloop.drafting import (
    InvalidApiKey,
    ProviderCallFailed,
    ProviderRateLimited,
    build_system_prompt,
    draft_answer,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/jobs", tags=["drafting"])

RATE_LIMIT_MAX_REQUESTS = 10
RATE_LIMIT_WINDOW_SECONDS = 60.0


class _RateLimiter:
    """Sliding-window per-key request counter. `key` is normally the
    client IP; tests use a fixed key so the 11th call in a window is
    deterministic regardless of what IP TestClient happens to report."""

    def __init__(self, max_requests: int, window_seconds: float) -> None:
        self._max_requests = max_requests
        self._window_seconds = window_seconds
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            cutoff = now - self._window_seconds
            while hits and hits[0] < cutoff:
                hits.popleft()
            if len(hits) >= self._max_requests:
                return False
            hits.append(now)
            return True

    def reset(self) -> None:
        """Test-only hook - clears all tracked state so one test's calls
        never bleed into the rate-limit window of another (the FastAPI
        `app` object, and therefore this limiter, is a module-level
        singleton shared across the whole test session)."""
        with self._lock:
            self._hits.clear()


_rate_limiter = _RateLimiter(RATE_LIMIT_MAX_REQUESTS, RATE_LIMIT_WINDOW_SECONDS)


def _enforce_rate_limit(request: Request) -> None:
    client_ip = request.client.host if request.client else "unknown"
    if not _rate_limiter.allow(client_ip):
        raise HTTPException(
            429,
            f"Rate limit exceeded for this endpoint ({RATE_LIMIT_MAX_REQUESTS} requests per "
            f"{int(RATE_LIMIT_WINDOW_SECONDS)}s per client). Try again shortly.",
        )


def _require_api_key(body: DraftAnswerRequest) -> str:
    if body.api_key is None or not body.api_key.strip():
        raise HTTPException(400, "api_key is required - this endpoint never falls back to a server-side key.")
    return body.api_key


def _lookup_job_and_active_resume(db: Session, job_id: int) -> tuple[JobPosting, Company, ResumeVersion]:
    row = db.execute(
        select(JobPosting, Company)
        .join(Company, JobPosting.company_id == Company.id)
        .where(JobPosting.id == job_id)
    ).first()
    if row is None:
        raise HTTPException(404, f"No job posting with id={job_id}")
    job, company = row.JobPosting, row.Company

    # Same "resolve the active resume fresh, at request time" pattern as
    # huntloop.api.routers.jobs._active_resume_embedding() - just reading
    # extracted_text instead of embedding.
    resume = db.query(ResumeVersion).filter_by(is_active=True).first()
    if resume is None:
        raise HTTPException(400, "No active resume is on file - upload one before drafting an answer.")

    return job, company, resume


@router.post("/{job_id}/draft-answer", response_model=DraftAnswerResponse)
def draft_job_answer(
    job_id: int,
    body: DraftAnswerRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> DraftAnswerResponse:
    _enforce_rate_limit(request)
    api_key = _require_api_key(body)
    job, company, resume = _lookup_job_and_active_resume(db, job_id)

    system_prompt = build_system_prompt(
        job_title=job.job_title,
        company_name=company.name,
        job_description=job.job_description,
        resume_text=resume.extracted_text,
    )

    try:
        answer = draft_answer(body.provider, api_key, system_prompt, body.prompt)
    except InvalidApiKey as e:
        raise HTTPException(401, f"The provider rejected this API key: {e}") from e
    except ProviderRateLimited as e:
        raise HTTPException(429, f"The provider reported a rate limit: {e}") from e
    except ProviderCallFailed as e:
        raise HTTPException(502, f"The provider call failed: {e}") from e

    return DraftAnswerResponse(answer=answer, provider=body.provider)
