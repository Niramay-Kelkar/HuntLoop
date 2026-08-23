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
    """A JobDataPipeline bound to the isolated test schema, emptied after each test."""
    pl = JobDataPipeline(test_database_url)

    yield pl

    with pl.Session() as session:
        for table in reversed(Base.metadata.sorted_tables):
            session.execute(table.delete())
        session.commit()


@pytest.fixture()
def db_session(pipeline):
    """A plain session on the same isolated schema, for making assertions."""
    session = pipeline.Session()
    yield session
    session.close()


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
