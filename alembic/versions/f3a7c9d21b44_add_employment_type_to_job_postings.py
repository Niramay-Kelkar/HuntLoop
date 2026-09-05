"""add employment_type to job_postings

Revision ID: f3a7c9d21b44
Revises: df1f114b5aee
Create Date: 2026-09-05 09:00:00.000000

Adds a nullable `employment_type` column to `job_postings` - the
normalized (Full-time/Part-time/Contract/Internship/Other) employment
type for a posting, per huntloop.employment_type.normalize_employment_type().

Nullable because not every source/posting carries this signal at all
(e.g. a Workday posting with an empty timeType, or a Greenhouse company
that never configured an "Employment Type" custom field) - see
CLAUDE.md/SESSIONS.md for the real per-source investigation. This
migration only adds the column; the scripts/backfill_employment_type_*.py
scripts (one per source's real data shape) fill it for existing rows.

Real schema-drift finding while writing this migration (same class of
issue `37f5b1de06fe` reconciled for other columns): this dev machine's
live `job_postings` table ALREADY has an `employment_type` column
(`VARCHAR(100)`), predating this task and untracked by any prior Alembic
migration - a leftover from this project's pre-Alembic
`Base.metadata.create_all()` days, per `db_models.py`'s own
`employment_type = scrapy.Field()` item field having existed unused since
early on (see CLAUDE.md's architectural-decisions section on Alembic
being the sole source of schema creation now). It has never held any
data (confirmed via a live query - 0/96,409 rows non-NULL; no code path
ever wrote to it before this task). Guarded the same way `37f5b1de06fe`
guarded its drifted `location` column: if the column already exists,
narrow it to this migration's real length (`VARCHAR(50)` - the
normalized values are always one of 5 short strings, well under 100
chars) instead of adding a duplicate; a fresh database with no prior
drift just gets the column added normally.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f3a7c9d21b44'
down_revision: Union[str, Sequence[str], None] = 'df1f114b5aee'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col['name']: col for col in inspector.get_columns('job_postings')}
    if 'employment_type' in columns:
        op.alter_column(
            'job_postings', 'employment_type',
            type_=sa.String(length=50),
            existing_type=sa.String(length=columns['employment_type']['type'].length),
            existing_nullable=True,
        )
    else:
        op.add_column('job_postings', sa.Column('employment_type', sa.String(length=50), nullable=True))


def downgrade() -> None:
    op.drop_column('job_postings', 'employment_type')
