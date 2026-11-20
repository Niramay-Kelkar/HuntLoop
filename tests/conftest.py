"""
Shared fixtures for the pytest suite.

Isolation strategy: rather than spinning up a separate Postgres instance
(e.g. via pytest-postgresql, which manages its own postgres/initdb binaries),
each test session creates a throwaway schema inside the same local Postgres
server the app already uses, and points the pipeline's engine at it via
`?options=-csearch_path=<schema>` on the connection URL. All tables get
created fresh in that schema and the schema is dropped at the end of the
run. This was simpler and more reliable to set up here because this
machine's Postgres already has permission quirks (see the AlterTable
ownership issues from the schema-reconciliation migration) that made
provisioning a whole second instance/cluster more fragile than reusing
the one that's already known to work; a same-server schema is also
guaranteed to behave identically to production (same Postgres version,
same extensions) since it *is* the same server. The tradeoff is that
tests share the server process with real data - but a Postgres schema is
a fully separate namespace, so the test schema's tables are physically
distinct from `public.job_postings` and friends; nothing here ever reads
from or writes to the real tables.
"""
import socket
import sys
import uuid

import pytest
from sqlalchemy import create_engine, text

from huntloop.settings import DATABASE_URL
from huntloop.db_models import Base
from huntloop.pipelines import JobDataPipeline


def _schema_scoped_url(schema_name: str) -> str:
    # Deliberately schema_name ONLY, never `,public` - tried adding public
    # to the search_path (2026-08-22, see SESSIONS.md) to make the
    # `vector` type, which lives in `public` (CREATE EXTENSION vector),
    # resolve for the new embedding columns. That broke isolation
    # instead: with `public` second, create_all()'s own has_table() check
    # resolves unqualified table names via search_path, finds the *real*
    # public.companies/job_postings/etc. (same names, schema_name has none
    # yet) before schema_name has any tables of its own, and silently
    # skips creating fresh ones there - leaving the "isolated" schema
    # empty and every test silently reading/writing real production data.
    # Caught by manually inspecting the schema after create_all()
    # (`\dt schema_name.*` came back empty) rather than trusting that
    # tests passing meant isolation was intact. Fixed at the source
    # instead (huntloop.db_models's Vector subclass schema-qualifies
    # `public.vector(...)` directly in its DDL), so this fixture never
    # needs `public` on the path at all.
    separator = "&" if "?" in DATABASE_URL else "?"
    return f"{DATABASE_URL}{separator}options=-csearch_path={schema_name}"


@pytest.fixture(scope="session")
def test_database_url():
    """A DATABASE_URL pointed at a fresh, isolated schema for this test run."""
    schema_name = f"pytest_{uuid.uuid4().hex[:8]}"

    admin_engine = create_engine(DATABASE_URL)
    with admin_engine.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema_name}"'))
    admin_engine.dispose()

    url = _schema_scoped_url(schema_name)
    schema_engine = create_engine(url)
    Base.metadata.create_all(schema_engine)
    schema_engine.dispose()

    yield url

    admin_engine = create_engine(DATABASE_URL)
    with admin_engine.begin() as conn:
        conn.execute(text(f'DROP SCHEMA "{schema_name}" CASCADE'))
    admin_engine.dispose()


@pytest.fixture()
def pipeline(test_database_url):
    """A JobDataPipeline bound to the isolated test schema, emptied after
    each test. `JobDataPipeline.__init__` calls `create_engine(...)`, so
    this fixture creates a brand new engine (and connection pool) per
    test function - `pl.engine.dispose()` below closes every pooled
    connection before the fixture returns, rather than leaving that to
    garbage collection.

    Before this was added, nothing closed the engine itself (only the
    session, via the `with` block). A SQLAlchemy Engine/Pool holds
    internal locks and event-listener registries that form reference
    cycles, so CPython's refcounting alone does not reclaim it - it
    waits on a full garbage-collection cycle, which does not necessarily
    run between every test. Across hundreds of tests the idle pooled
    connections from old, already-finished tests piled up and eventually
    exhausted Postgres's max_connections (reproduced directly: a fresh
    Postgres container with no other client, max_connections 100,
    running this suite climbed to 97 connections, and pg_stat_activity
    showed dozens of idle connections whose last query was this
    teardown's own COMMIT - see SESSIONS.md for the full
    investigation)."""
    pl = JobDataPipeline(test_database_url)

    yield pl

    with pl.Session() as session:
        for table in reversed(Base.metadata.sorted_tables):
            session.execute(table.delete())
        session.commit()

    pl.engine.dispose()


@pytest.fixture()
def db_session(pipeline):
    """A plain session on the same isolated schema, for making assertions."""
    session = pipeline.Session()
    yield session
    session.close()


# A value that is obviously not a real credential, used in place of
# every third-party key for the whole suite - see
# _no_real_provider_credentials below. Tests may compare against this
# constant (a boolean assertion); no test should ever print a resolved
# credential value, real or sentinel.
TEST_CREDENTIAL_SENTINEL = "huntloop-test-sentinel-not-a-real-credential"

# Every environment variable that holds a third-party credential this
# project can spend real money / real send-allowance through. All of
# them are forced to the sentinel for every test - see the fixture.
SENTINEL_CREDENTIAL_ENV_VARS = (
    # Email / notification delivery (Knock triggers the send, Resend
    # delivers it)
    "RESEND_API_KEY",
    "KNOCK_API_KEY",
    # Paid/quota-limited LLM and search providers
    "GROQ_API_KEY",
    "GEMINI_API_KEY",
    "MISTRAL_API_KEY",
    "CEREBRAS_API_KEY",
    "TAVILY_API_KEY",
    # Modal (remote compute, billed against a monthly credit budget)
    "MODAL_TOKEN_ID",
    "MODAL_TOKEN_SECRET",
)

# The same credentials again, as module-level constants captured at
# import time. Clearing the environment variable alone is not enough for
# these: whichever module imported first already read the real value out
# of the real .env (every one of these modules, and
# huntloop.knock_client/huntloop.resend_client, call load_dotenv() at
# import time), so the real key survives in the module's own attribute.
# Only modules already imported are touched - a module imported later
# reads the already-sentinelled environment anyway.
SENTINEL_CREDENTIAL_MODULE_ATTRS = (
    ("huntloop.skills_matching", "GROQ_API_KEY"),
    ("huntloop.skills_matching_groq_120b", "GROQ_API_KEY"),
    ("huntloop.skills_matching_gemini", "GEMINI_API_KEY"),
    ("huntloop.skills_matching_mistral", "MISTRAL_API_KEY"),
    ("huntloop.resume_ats_report", "GROQ_API_KEY"),
    ("huntloop.resume_ats_report", "GEMINI_API_KEY"),
    ("huntloop.feedback_triage", "GROQ_API_KEY"),
    ("huntloop.feedback_triage", "GEMINI_API_KEY"),
    ("huntloop.company_research", "TAVILY_API_KEY"),
)

# Hostnames (matched as suffixes, case-insensitively) that no test may
# ever resolve, let alone connect to. These are the real paid/live
# services this project talks to; the local database and anything else
# on localhost is deliberately NOT in this list.
BLOCKED_PROVIDER_HOST_SUFFIXES = (
    "resend.com",
    "knock.app",
    "tavily.com",
    "groq.com",
    "generativelanguage.googleapis.com",
    "mistral.ai",
    "cerebras.ai",
    "modal.com",
)


class BlockedProviderCall(RuntimeError):
    """Raised instead of resolving/connecting to a real provider host."""


def _is_blocked_host(host) -> bool:
    if not isinstance(host, str):
        return False
    host = host.strip().strip(".").lower()
    return any(host == s or host.endswith("." + s) for s in BLOCKED_PROVIDER_HOST_SUFFIXES)


@pytest.fixture(autouse=True)
def _no_real_provider_credentials(monkeypatch, request):
    """Project-wide safety net making it impossible for the test suite to
    send a real email, trigger a real notification, or spend real LLM /
    search / compute credit - whether or not an individual test remembers
    to mock anything.

    Added 2026-11-20 after real alert emails ("HuntLoop: modal budget at
    80% / 95%") kept arriving in the owner's inbox on exactly the days
    the suite had been run. The 2026-10-09 fixture this replaces deleted
    RESEND_API_KEY only, which was the whole sending path at the time;
    once the alert path was rewired through Knock
    (huntloop.budget_alerts -> huntloop.knock_client.trigger_workflow,
    which posts to api.knock.app and lets Knock deliver via its
    Resend-backed channel), KNOCK_API_KEY became the credential that
    mattered and nothing suite-wide cleared it. Two Modal ledger tests
    (test_reserve_refuses_rather_than_overshooting_the_cap and
    test_reserve_allows_a_weight_that_lands_exactly_on_the_cap in
    tests/test_modal_resume_processing.py) drive a fake 10-invocation cap
    to 80% and 100%, which crosses the real alert thresholds; the alert
    failure path swallows every exception, so the sends were completely
    invisible in the suite's own output.

    Two independent layers, deliberately, since either alone has a hole:

    (a) Every credential environment variable, and every module-level
        constant that already captured one at import time, is replaced
        with TEST_CREDENTIAL_SENTINEL. Clearing the variable is not
        enough on its own: these modules call load_dotenv() at import,
        so a module imported mid-test re-reads the real .env.

    (b) DNS resolution and socket connection to the real provider hosts
        is blocked outright, with a message naming the test that tried
        it. This is the layer that actually guarantees nothing leaves the
        machine - a sentinel key would otherwise still produce a real
        HTTP request that merely gets rejected, which still contacts the
        provider.

    Tests that deliberately exercise a sending path keep working exactly
    as before, by setting their own obviously-fake key and mocking the
    transport (tests/test_knock_client.py, tests/test_resend_client.py),
    or by stubbing the alert/sender function itself. Do not remove this
    fixture or make it opt-in/per-file.
    """
    for var in SENTINEL_CREDENTIAL_ENV_VARS:
        monkeypatch.setenv(var, TEST_CREDENTIAL_SENTINEL)

    for module_name, attr in SENTINEL_CREDENTIAL_MODULE_ATTRS:
        module = sys.modules.get(module_name)
        if module is not None and getattr(module, attr, None) is not None:
            monkeypatch.setattr(module, attr, TEST_CREDENTIAL_SENTINEL, raising=False)

    test_id = request.node.nodeid
    real_getaddrinfo = socket.getaddrinfo
    real_create_connection = socket.create_connection

    def _guarded_getaddrinfo(host, *args, **kwargs):
        if _is_blocked_host(host):
            raise BlockedProviderCall(
                f"Blocked an outbound call to a real provider host from {test_id} - "
                f"the test suite may not contact live paid services. Mock the sender "
                f"(see tests/conftest.py's _no_real_provider_credentials)."
            )
        return real_getaddrinfo(host, *args, **kwargs)

    def _guarded_create_connection(address, *args, **kwargs):
        if isinstance(address, tuple) and address and _is_blocked_host(address[0]):
            raise BlockedProviderCall(
                f"Blocked an outbound connection to a real provider host from {test_id} - "
                f"the test suite may not contact live paid services."
            )
        return real_create_connection(address, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", _guarded_getaddrinfo)
    monkeypatch.setattr(socket, "create_connection", _guarded_create_connection)


# Historical note (2026-10-09, the "Resend test-isolation incident" in
# SESSIONS.md): an earlier, narrower version of the fixture above
# deleted RESEND_API_KEY only, after a suite run sent 16 real emails
# through the then-current direct-Resend sending path. That incident is
# why this protection exists at all; the 2026-11-20 rewrite above
# generalised it to every provider credential plus an outbound-network
# block, after the same class of leak recurred through Knock.


@pytest.fixture()
def api_client(db_session):
    """A FastAPI TestClient for huntloop.api.main.app, with its DB session
    dependency overridden to use the isolated test schema (db_session)
    instead of huntloop.api.dependencies.get_db's real module-level
    engine, which is bound to the real DATABASE_URL - so API requests
    made through this client never touch real production data, same
    guarantee as every other fixture here."""
    from fastapi.testclient import TestClient

    from huntloop.api.dependencies import get_db
    from huntloop.api.main import app

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture()
def demo_api_client(db_session, monkeypatch):
    """A FastAPI TestClient for a fresh demo-mode app, built via
    huntloop.api.main.create_app() rather than the shared module-level
    `app` the plain api_client fixture uses - each test gets its own app
    instance (and its own demo rate limiter, see huntloop.api.main),
    pointed at the same isolated test schema as every other fixture."""
    from fastapi.testclient import TestClient

    from huntloop.api.dependencies import get_db
    from huntloop.api.main import create_app

    monkeypatch.setenv("DEMO_MODE", "true")
    demo_app = create_app()

    def _override_get_db():
        yield db_session

    demo_app.dependency_overrides[get_db] = _override_get_db
    yield TestClient(demo_app)
