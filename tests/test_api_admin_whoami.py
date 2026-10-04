"""
Tests for GET /admin/whoami (huntloop.api.routers.admin) - the
frontend nav's "should I show the admin link" check. Same mocking
convention as tests/test_api_admin_feedback.py: real Clerk calls are
monkeypatched, "no session" is exercised against the real require_admin()
code path.
"""
from clerk_backend_api.security.types import AuthStatus, RequestState

import huntloop.api.admin_auth as admin_auth


def _mock_signed_in(monkeypatch, *, user_id: str = "user_123", email: str = "niramayrkelkar@gmail.com") -> None:
    monkeypatch.setattr(
        admin_auth,
        "_authenticate_request_state",
        lambda request: RequestState(status=AuthStatus.SIGNED_IN, payload={"sub": user_id}),
    )
    monkeypatch.setattr(admin_auth, "_fetch_primary_email", lambda uid: email)


def test_whoami_with_no_session_returns_401(api_client, monkeypatch):
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_dummy_0000000000000000000000000000")
    resp = api_client.get("/admin/whoami")
    assert resp.status_code == 401


def test_whoami_with_wrong_email_returns_403(api_client, monkeypatch):
    monkeypatch.setenv("ADMIN_ALLOWED_EMAILS", "niramayrkelkar@gmail.com")
    _mock_signed_in(monkeypatch, email="someone-else@example.com")
    resp = api_client.get("/admin/whoami", headers={"Authorization": "Bearer faketoken"})
    assert resp.status_code == 403


def test_whoami_with_valid_admin_session_returns_email(api_client, monkeypatch):
    monkeypatch.setenv("ADMIN_ALLOWED_EMAILS", "niramayrkelkar@gmail.com")
    _mock_signed_in(monkeypatch, email="niramayrkelkar@gmail.com")
    resp = api_client.get("/admin/whoami", headers={"Authorization": "Bearer faketoken"})
    assert resp.status_code == 200
    assert resp.json() == {"email": "niramayrkelkar@gmail.com"}
