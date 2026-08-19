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
