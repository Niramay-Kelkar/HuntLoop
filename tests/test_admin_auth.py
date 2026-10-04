"""
Unit tests for huntloop.api.admin_auth's env-var resolution helpers -
specifically _authorized_parties(), whose wrong fallback was the real
root cause of a production incident (see SESSIONS.md "Admin feedback
page failed to load on the live demo"): CLERK_AUTHORIZED_PARTIES was
never set on Render, and the old code fell all the way back to a
localhost-only default instead of the already-correctly-configured
CORS_ALLOWED_ORIGINS - so every real admin session's Clerk token failed
the SDK's own azp/authorized-party check with a 401, before the
email-allowlist check ever ran. These tests exercise the real
precedence logic directly, without needing a real signed Clerk token -
the account that caught the bug in test_api_admin_feedback.py never
exercises this at all, since it monkeypatches _authenticate_request_state
itself and so never calls into the real authorized_parties codepath.
"""
import huntloop.api.admin_auth as admin_auth


def test_authorized_parties_defaults_to_cors_allowed_origins(monkeypatch):
    monkeypatch.delenv("CLERK_AUTHORIZED_PARTIES", raising=False)
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://huntloop-demo.vercel.app")
    assert admin_auth._authorized_parties() == ["https://huntloop-demo.vercel.app"]


def test_authorized_parties_prefers_explicit_clerk_var_over_cors(monkeypatch):
    monkeypatch.setenv("CLERK_AUTHORIZED_PARTIES", "https://admin.example.com")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://huntloop-demo.vercel.app")
    assert admin_auth._authorized_parties() == ["https://admin.example.com"]


def test_authorized_parties_falls_back_to_localhost_when_both_unset(monkeypatch):
    monkeypatch.delenv("CLERK_AUTHORIZED_PARTIES", raising=False)
    monkeypatch.delenv("CORS_ALLOWED_ORIGINS", raising=False)
    assert admin_auth._authorized_parties() == [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]


def test_authorized_parties_splits_multiple_cors_origins(monkeypatch):
    monkeypatch.delenv("CLERK_AUTHORIZED_PARTIES", raising=False)
    monkeypatch.setenv(
        "CORS_ALLOWED_ORIGINS", "https://huntloop-demo.vercel.app,http://localhost:3000"
    )
    assert admin_auth._authorized_parties() == [
        "https://huntloop-demo.vercel.app",
        "http://localhost:3000",
    ]
