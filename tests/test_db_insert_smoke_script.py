"""
Regression test for the recurring job_sources duplication: the manual
smoke-test script (src/huntloop/test_db_insert.py, run by hand, not
collected by pytest itself - see its own module docstring) used to
hardcode the literal source name "Greenhouse", separate from and
different than the real GreenhouseScraper spider's own `self.name`
("greenhouse_api") that JobDataPipeline actually uses. Phase 0 (see
SESSIONS.md, alembic/versions/37f5b1de06fe) consolidated the resulting
duplicate job_sources rows once, but only fixed the data - the script
kept hardcoding "Greenhouse" and recreated the duplicate on its next run.

This test runs the real smoke-test script's actual insert path (not a
reimplementation of its logic) against the isolated test schema, after
seeding it with the JobSource row the real pipeline convention would
already have created, and asserts the script reuses that row rather than
creating a second one under a different name. See the test at the bottom
of this file for confirmation that this test would have failed against
the pre-fix behavior.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from huntloop import test_db_insert
from huntloop.db_models import JobSource
from huntloop.spiders.greenhouse_spider import GreenhouseScraper


def test_smoke_script_reuses_the_pipelines_job_source_row(test_database_url, monkeypatch):
    # Point the script's own DATABASE_URL global at the isolated test
    # schema, so this never touches the real database - main() reads this
    # module-level name at call time, so patching it here is enough.
    monkeypatch.setattr("huntloop.test_db_insert.DATABASE_URL", test_database_url)

    engine = create_engine(test_database_url)
    Session = sessionmaker(bind=engine)

    # Seed the schema with the JobSource row the real pipeline would have
    # already created for a genuine Greenhouse scrape, via the real
    # spider's own naming convention.
    session = Session()
    session.add(JobSource(name=GreenhouseScraper.name))
    session.commit()
    session.close()

    # Run the actual smoke-test script's insert path.
    test_db_insert.main()

    session = Session()
    try:
        sources = session.query(JobSource).all()
        greenhouse_like = [s.name for s in sources if "greenhouse" in s.name.lower()]
    finally:
        session.close()

    # Exactly one Greenhouse-flavored row, matching the real spider's
    # convention - not a second one under a separately hardcoded name.
    assert greenhouse_like == [GreenhouseScraper.name]
