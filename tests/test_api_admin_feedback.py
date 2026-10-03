"""
Tests for the admin-only feedback review API
(huntloop.api.routers.admin_feedback) and the auth it sits behind
(huntloop.api.admin_auth).

Every real Clerk call is mocked, same convention this project already
uses for every other external provider (Groq/Gemini/Mistral/Tavily) -
see CLAUDE.md. "No session" is exercised against the REAL
require_admin() code path (no Authorization header never reaches
Clerk's network at all - confirmed directly: authenticate_request()
resolves to signed-out purely from the missing token, before any
network call), with only CLERK_SECRET_KEY stubbed to a well-formed
dummy value so _clerk_client() doesn't 500 first. "Valid admin" and
"wrong email" monkeypatch _authenticate_request_state/_fetch_primary_email
directly, so they don't need a real signed Clerk session token.
"""
from clerk_backend_api.security.types import AuthStatus, RequestState

import huntloop.api.admin_auth as admin_auth
from huntloop.db_models import Feedback, FeedbackCategory, FeedbackStatus, FeedbackTriageStatus


def _seed_feedback(db_session) -> Feedback:
    row = Feedback(
        category=FeedbackCategory.BUG,
        raw_text="The export button is broken on Safari.",
        triage_status=FeedbackTriageStatus.PENDING,
        status=FeedbackStatus.OPEN,
        is_public=False,
    )
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


def _mock_signed_in(monkeypatch, *, user_id: str = "user_123", email: str = "niramayrkelkar@gmail.com") -> None:
    monkeypatch.setattr(
        admin_auth,
        "_authenticate_request_state",
        lambda request: RequestState(status=AuthStatus.SIGNED_IN, payload={"sub": user_id}),
    )
    monkeypatch.setattr(admin_auth, "_fetch_primary_email", lambda uid: email)


# ---------------------------------------------------------------------------
# require_admin() itself
# ---------------------------------------------------------------------------


def test_admin_route_with_no_session_returns_401(api_client, monkeypatch):
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_dummy_0000000000000000000000000000")
    resp = api_client.get("/admin/feedback")
    assert resp.status_code == 401


def test_admin_route_with_no_clerk_secret_key_returns_500(api_client, monkeypatch):
    monkeypatch.delenv("CLERK_SECRET_KEY", raising=False)
    resp = api_client.get("/admin/feedback")
    assert resp.status_code == 500


def test_admin_route_with_wrong_email_returns_403(api_client, monkeypatch):
    monkeypatch.setenv("ADMIN_ALLOWED_EMAILS", "niramayrkelkar@gmail.com")
    _mock_signed_in(monkeypatch, email="someone-else@example.com")
    resp = api_client.get("/admin/feedback", headers={"Authorization": "Bearer faketoken"})
    assert resp.status_code == 403


def test_admin_route_with_valid_admin_session_succeeds(api_client, db_session, monkeypatch):
    monkeypatch.setenv("ADMIN_ALLOWED_EMAILS", "niramayrkelkar@gmail.com")
    _mock_signed_in(monkeypatch, email="niramayrkelkar@gmail.com")
    _seed_feedback(db_session)

    resp = api_client.get("/admin/feedback", headers={"Authorization": "Bearer faketoken"})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    # raw_text must actually be present - the entire point of this
    # endpoint vs. the public one.
    assert body[0]["raw_text"] == "The export button is broken on Safari."
    assert body[0]["is_public"] is False


def test_admin_allowed_emails_is_case_insensitive(api_client, db_session, monkeypatch):
    monkeypatch.setenv("ADMIN_ALLOWED_EMAILS", "Niramayrkelkar@Gmail.com")
    _mock_signed_in(monkeypatch, email="niramayrkelkar@gmail.com")
    _seed_feedback(db_session)

    resp = api_client.get("/admin/feedback", headers={"Authorization": "Bearer faketoken"})
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# PATCH /admin/feedback/{id}
# ---------------------------------------------------------------------------


def test_admin_patch_updates_status_and_is_public(api_client, db_session, monkeypatch):
    monkeypatch.setenv("ADMIN_ALLOWED_EMAILS", "niramayrkelkar@gmail.com")
    _mock_signed_in(monkeypatch)
    row = _seed_feedback(db_session)

    resp = api_client.patch(
        f"/admin/feedback/{row.id}",
        json={"status": "resolved", "is_public": True},
        headers={"Authorization": "Bearer faketoken"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "resolved"
    assert body["is_public"] is True

    db_session.expire_all()
    persisted = db_session.query(Feedback).filter_by(id=row.id).first()
    assert persisted.status == FeedbackStatus.RESOLVED
    assert persisted.is_public is True


def test_admin_patch_requires_a_real_session_too(api_client, db_session, monkeypatch):
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_dummy_0000000000000000000000000000")
    row = _seed_feedback(db_session)

    resp = api_client.patch(f"/admin/feedback/{row.id}", json={"status": "resolved"})
    assert resp.status_code == 401

    db_session.expire_all()
    persisted = db_session.query(Feedback).filter_by(id=row.id).first()
    assert persisted.status == FeedbackStatus.OPEN


def test_admin_patch_404_for_missing_row(api_client, monkeypatch):
    monkeypatch.setenv("ADMIN_ALLOWED_EMAILS", "niramayrkelkar@gmail.com")
    _mock_signed_in(monkeypatch)

    resp = api_client.patch(
        "/admin/feedback/999999",
        json={"status": "resolved"},
        headers={"Authorization": "Bearer faketoken"},
    )
    assert resp.status_code == 404


def test_admin_patch_400_with_nothing_to_update(api_client, db_session, monkeypatch):
    monkeypatch.setenv("ADMIN_ALLOWED_EMAILS", "niramayrkelkar@gmail.com")
    _mock_signed_in(monkeypatch)
    row = _seed_feedback(db_session)

    resp = api_client.patch(
        f"/admin/feedback/{row.id}", json={}, headers={"Authorization": "Bearer faketoken"}
    )
    assert resp.status_code == 400


def test_admin_patch_400_for_invalid_status(api_client, db_session, monkeypatch):
    monkeypatch.setenv("ADMIN_ALLOWED_EMAILS", "niramayrkelkar@gmail.com")
    _mock_signed_in(monkeypatch)
    row = _seed_feedback(db_session)

    resp = api_client.patch(
        f"/admin/feedback/{row.id}",
        json={"status": "not_a_real_status"},
        headers={"Authorization": "Bearer faketoken"},
    )
    assert resp.status_code == 400
