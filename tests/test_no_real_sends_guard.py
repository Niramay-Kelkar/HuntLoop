"""
Guard tests for tests/conftest.py's _no_real_provider_credentials
fixture - the suite-wide protection that makes it impossible for a test
to send a real email, trigger a real Knock notification, or spend real
LLM / search / compute credit.

Added 2026-11-20, after real "HuntLoop: modal budget at 80% / 95%"
alert emails turned out to be coming from the test suite itself (see
that fixture's docstring and SESSIONS.md). The protection is only worth
having if it is itself tested, so these tests prove both halves of it:
the credentials a test actually sees, and the outbound-network block.

These tests assert booleans and identity against the sentinel constant.
They never print a resolved credential value, real or otherwise.
"""
import os
import socket

import pytest

from huntloop.knock_client import KnockUnavailable, trigger_workflow
from conftest import (
    SENTINEL_CREDENTIAL_ENV_VARS,
    TEST_CREDENTIAL_SENTINEL,
    BlockedProviderCall,
)


@pytest.mark.parametrize("var", SENTINEL_CREDENTIAL_ENV_VARS)
def test_every_provider_credential_is_the_sentinel_inside_a_test(var):
    """The value the code would resolve at runtime is the sentinel, not
    whatever real credential sits in this machine's .env."""
    assert os.getenv(var) == TEST_CREDENTIAL_SENTINEL


@pytest.mark.parametrize(
    "host",
    [
        "api.knock.app",
        "api.resend.com",
        "api.tavily.com",
        "api.groq.com",
        "generativelanguage.googleapis.com",
        "api.modal.com",
    ],
)
def test_resolving_a_real_provider_host_is_blocked(host):
    with pytest.raises(BlockedProviderCall) as exc_info:
        socket.getaddrinfo(host, 443)

    # The failure must name the test that attempted it, so a future
    # offender is obvious from the error alone.
    assert "test_resolving_a_real_provider_host_is_blocked" in str(exc_info.value)


def test_localhost_resolution_still_works():
    """The block is targeted at live provider hosts only - the local
    database and anything else on localhost must be unaffected."""
    assert socket.getaddrinfo("127.0.0.1", 5432)


def test_reaching_the_real_knock_sender_is_blocked_not_sent(db_session):
    """The end-to-end case this whole fixture exists for: a sender call
    that nothing mocked. Knock reads as "configured" (the sentinel key is
    a non-empty string), so this gets all the way to the real
    requests.post - and is stopped there, surfacing as a trigger_error
    rather than a delivered notification."""
    with pytest.raises(KnockUnavailable) as exc_info:
        trigger_workflow(
            db_session,
            workflow_key="budget-threshold-alert",
            to="owner@example.com",
            subject="guard test - must never be delivered",
            html="<p>guard test</p>",
        )

    assert exc_info.value.reason == "trigger_error"
    assert "BlockedProviderCall" in str(exc_info.value)
