"""
Admin authentication for the feedback-review admin API
(huntloop.api.routers.admin_feedback) - replaces the terminal-only
scripts/review_feedback.py workflow with a real web UI
(frontend/src/app/admin/feedback/page.tsx), gated behind a real Clerk
session plus an email allowlist.

Two layers, BOTH required - this is deliberate defense in depth, per
the task that introduced it: the point is that only explicitly-named
operators can reach admin data, not anyone who signs up for a Clerk
account on the public demo site, so a merely-valid Clerk session is
NOT sufficient on its own.

  1. A valid Clerk session, verified server-side via Clerk's own
     official Python SDK (`clerk-backend-api`'s `Clerk.authenticate_request()`)
     - no hand-rolled JWT verification, no manual JWKS fetching/caching.
  2. The signed-in user's real primary email address is on the
     ADMIN_ALLOWED_EMAILS allowlist (comma-separated env var, defaults
     to just the project owner's own address).

Email is fetched from Clerk's Users API by the verified token's `sub`
claim (_fetch_primary_email), rather than trusted from a claim on the
session token itself - a default Clerk session token (v1, and v2
without "Customize session token" configured) does not carry email at
all, so relying on a token claim would silently admit nobody (or need
every deployment to remember a non-default Clerk dashboard setting).
Fetching the real user record is the one approach that works
regardless of how the Clerk instance's session token is configured.

The frontend sends the Clerk session token as `Authorization: Bearer
<token>` (see frontend/src/lib/adminApi.ts) - this backend is a
separate service from the Next.js app, the same shape Clerk's own docs
describe for authenticating a request from a Clerk frontend SDK to "a
Python backend (Django, Flask, and other Python web frameworks)".

_authenticate_request_state() / _fetch_primary_email() are split out
from require_admin() specifically so tests can monkeypatch them
directly (same convention this project already uses to mock every real
external-provider call - see huntloop.skills_matching*'s test mocks)
rather than needing a real Clerk account/session token to exercise the
admin endpoints end to end.
"""
import logging
import os
from dataclasses import dataclass

from clerk_backend_api import Clerk
from clerk_backend_api.security.types import AuthenticateRequestOptions, RequestState
from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)

# Same default origins huntloop.api.main's CORS_ALLOWED_ORIGINS already
# uses - kept as a separate env var (not read from CORS_ALLOWED_ORIGINS
# directly) since the two are conceptually different allowlists (CORS
# vs. Clerk's own replay-protection "authorized parties" check) that
# happen to usually be the same set of origins; see .env.example.
_DEFAULT_AUTHORIZED_PARTIES = "http://localhost:3000,http://127.0.0.1:3000"
_DEFAULT_ADMIN_ALLOWED_EMAILS = "niramayrkelkar@gmail.com"


@dataclass(frozen=True)
class AdminUser:
    """The verified, allow-listed caller - returned by require_admin()
    and injected into every /admin/feedback route for logging."""

    user_id: str
    email: str


def _clerk_client() -> Clerk:
    # Read fresh (not cached) so a test can monkeypatch
    # os.environ/CLERK_SECRET_KEY between cases without worrying about a
    # stale cached client - this is only ever called a handful of times
    # per request on a low-traffic admin endpoint, so there's no real
    # cost to not caching it.
    secret_key = os.getenv("CLERK_SECRET_KEY")
    if not secret_key:
        raise RuntimeError(
            "CLERK_SECRET_KEY is not set - required to verify admin sessions "
            "for the /admin/feedback endpoints. See .env.example."
        )
    return Clerk(bearer_auth=secret_key)


def _authorized_parties() -> list[str]:
    raw = os.getenv("CLERK_AUTHORIZED_PARTIES", _DEFAULT_AUTHORIZED_PARTIES)
    return [p.strip() for p in raw.split(",") if p.strip()]


def _authenticate_request_state(request: Request) -> RequestState:
    """Verifies the request's Clerk session token (from its Authorization
    header) against Clerk's JWKS - real cryptographic verification via
    the SDK, not just "a token was present". Split out so tests can
    monkeypatch it with a fake RequestState instead of needing a real
    signed Clerk session token."""
    return _clerk_client().authenticate_request(
        request, AuthenticateRequestOptions(authorized_parties=_authorized_parties())
    )


def _fetch_primary_email(user_id: str) -> str | None:
    """Looks up the real, current primary email for a verified Clerk
    user id via Clerk's Users API - see this module's docstring for why
    this isn't just read off the session token's own claims."""
    user = _clerk_client().users.get(user_id=user_id)
    for address in user.email_addresses:
        if address.id == user.primary_email_address_id:
            return address.email_address
    # Fall back to the first email on file if, somehow, none matches
    # primary_email_address_id (e.g. it was just changed) - still a
    # real, Clerk-verified address, never a guess.
    if user.email_addresses:
        return user.email_addresses[0].email_address
    return None


def _allowed_emails() -> set[str]:
    raw = os.getenv("ADMIN_ALLOWED_EMAILS", _DEFAULT_ADMIN_ALLOWED_EMAILS)
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def require_admin(request: Request) -> AdminUser:
    """FastAPI dependency - raises 500 if admin auth itself isn't
    configured (CLERK_SECRET_KEY unset), 401 if there's no valid Clerk
    session, 403 if the session is valid but its email isn't
    allow-listed. Only a return value means both checks passed."""
    try:
        request_state = _authenticate_request_state(request)
    except RuntimeError as exc:
        # Misconfiguration (missing CLERK_SECRET_KEY), not a bad/missing
        # caller token - a 500, not a 401.
        raise HTTPException(500, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - any Clerk/network failure is "can't verify", never a crash
        logger.warning("Clerk session verification failed: %s", exc)
        raise HTTPException(401, "Invalid or missing admin session.") from exc

    if not request_state.is_signed_in:
        raise HTTPException(401, "Invalid or missing admin session.")

    user_id = (request_state.payload or {}).get("sub")
    if not user_id:
        raise HTTPException(401, "Invalid or missing admin session.")

    try:
        email = _fetch_primary_email(user_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to fetch Clerk user %s: %s", user_id, exc)
        raise HTTPException(401, "Unable to verify admin session.") from exc

    if not email or email.lower() not in _allowed_emails():
        logger.warning("Rejected admin request from non-allow-listed Clerk user %s (%s)", user_id, email)
        raise HTTPException(403, "This Clerk account is not authorized for admin access.")

    return AdminUser(user_id=user_id, email=email)
