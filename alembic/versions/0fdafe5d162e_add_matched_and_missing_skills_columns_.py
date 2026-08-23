"""add matched and missing skills columns to job_postings

Revision ID: 0fdafe5d162e
Revises: 08af7f0a020c
Create Date: 2026-08-22 15:31:53.216350

Adds nullable `matched_skills`/`missing_skills` JSON columns to
`job_postings` - precomputed and stored by
scripts/backfill_skills_matching.py (huntloop.skills_matching.match_skills()),
not recomputed live per view. Nullable because not every row has been
backfilled, and a per-row API failure during backfill leaves that row's
two columns NULL rather than aborting the whole run.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0fdafe5d162e'
down_revision: Union[str, Sequence[str], None] = '08af7f0a020c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('job_postings', sa.Column('matched_skills', sa.JSON(), nullable=True))
    op.add_column('job_postings', sa.Column('missing_skills', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('job_postings', 'missing_skills')
    op.drop_column('job_postings', 'matched_skills')
