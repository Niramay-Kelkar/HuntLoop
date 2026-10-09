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


@pytest.fixture(autouse=True)
def _no_real_resend_api_key(monkeypatch):
    """Project-wide safety net, added 2026-10-09 after a real incident -
    see SESSIONS.md's "Resend test-isolation incident" entry for the
    full writeup. Running the test suite locally during Resend Phase B
    development sent 16 REAL emails through a live Resend API key: two
    pre-existing Modal tests (test_reserve_refuses_once_budget_is_reached_and_does_not_increment,
    test_invoke_modal_raises_once_budget_is_reached in
    tests/test_modal_resume_processing.py) legitimately drive a fake
    budget to exactly 100% of a tiny cap in one call - which, once Phase
    B wired a real alert-email side effect into that same code path,
    means they also cross both the 80% and 95% alert thresholds for
    real. Neither test mocked huntloop.resend_client.send_email or
    huntloop.budget_alerts.check_and_alert_budget_threshold, and nothing
    suite-wide prevented the real RESEND_API_KEY already sitting in this
    project's real .env from being live in os.environ during that test -
    huntloop.resend_client calls load_dotenv() at import time, so the
    real key was present for every test in the suite except the one file
    (tests/test_resend_client.py) that happened to have its own
    autouse fixture deleting it, which is also the file Phase A's own
    author incorrectly assumed would set precedent project-wide.

    This fixture is the structural fix: it deletes RESEND_API_KEY from
    the environment before EVERY test in the entire suite runs,
    regardless of file, regardless of whether that test remembers to
    mock anything itself. huntloop.resend_client.send_email() always
    checks `_configured()` (RESEND_API_KEY present) FIRST, before any
    network code runs - so with this fixture in place, a test can only
    ever reach a real `resend.Emails.send()` call if it explicitly sets
    its own key back (e.g. tests/test_resend_client.py's `configured`
    fixture, which sets a deliberately fake "re_test_key" value, never
    a real one) AND fails to mock the send call itself - both of those
    remain an individual test's own explicit choice, not something that
    can happen by omission or by a future test author not knowing this
    history.

    This is a deliberate safety net making a real send *structurally
    impossible by default*, not just a style preference - do not remove
    it or make it opt-in/per-file. It was verified, not just written:
    see SESSIONS.md for the full-suite run with resend.Emails.send
    itself temporarily patched to raise on any unmocked call, confirming
    zero tests reach it for real."""
    monkeypatch.delenv("RESEND_API_KEY", raising=False)


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
