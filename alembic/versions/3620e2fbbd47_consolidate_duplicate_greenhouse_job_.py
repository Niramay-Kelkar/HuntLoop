"""consolidate duplicate greenhouse job source

Revision ID: 3620e2fbbd47
Revises: a89474d578f7
Create Date: 2026-08-21 12:18:29.735913

Root-cause follow-up to the job_sources consolidation done in
37f5b1de06fe ("reconcile schema drift"). That migration merged
"greenhouse_api" into "Greenhouse" and deleted "greenhouse_api" - but it
only fixed the data, not the code that kept recreating the duplicate:
src/huntloop/test_db_insert.py hardcoded the literal source name
"Greenhouse", while the real GreenhouseScraper spider has always used its
own `self.name` ("greenhouse_api") via JobDataPipeline. Every real scrape
after that migration silently recreated a "greenhouse_api" row, so the
duplication came right back (confirmed live: job_sources had both
`(1, 'Greenhouse')` and `(3, 'greenhouse_api')` again before this
migration).

This time the code is fixed first (test_db_insert.py now reads
GreenhouseScraper.name instead of hardcoding a separate literal - see
SESSIONS.md 2026-08-21), so nothing will ever create a "Greenhouse"-named
row again. That makes "greenhouse_api" - the real spider's and pipeline's
actual naming convention - the canonical row going forward, which is the
opposite consolidation direction from 37f5b1de06fe (which merged into
"Greenhouse"). Reassigns job_postings.source_id from "Greenhouse" to
"greenhouse_api", then deletes the now-orphaned "Greenhouse" row.

Not reversible for the same reason 37f5b1de06fe's data cleanup wasn't -
there is no record of which rows used to point at the deleted source.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3620e2fbbd47'
down_revision: Union[str, Sequence[str], None] = 'a89474d578f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Guarded with WHERE EXISTS checks so this migration runs cleanly
    # against a fresh database that never had the duplicate rows.
    op.execute(sa.text("""
        UPDATE job_postings
        SET source_id = (SELECT id FROM job_sources WHERE name = 'greenhouse_api')
        WHERE source_id = (SELECT id FROM job_sources WHERE name = 'Greenhouse')
          AND EXISTS (SELECT 1 FROM job_sources WHERE name = 'greenhouse_api')
    """))
    # Only drop the duplicate if there's actually a "greenhouse_api" row to
    # have consolidated into - on a database that only ever had the
    # "Greenhouse" name (e.g. never ran the real spider), leave it alone
    # rather than deleting the sole source row out from under it.
    op.execute(sa.text("""
        DELETE FROM job_sources
        WHERE name = 'Greenhouse'
          AND EXISTS (SELECT 1 FROM job_sources WHERE name = 'greenhouse_api')
    """))


def downgrade() -> None:
    raise NotImplementedError(
        "The data cleanup in this migration (reassigning job_postings.source_id "
        "off the deleted 'Greenhouse' job_sources row) is irreversible - there is "
        "no record of which rows previously pointed at it."
    )
