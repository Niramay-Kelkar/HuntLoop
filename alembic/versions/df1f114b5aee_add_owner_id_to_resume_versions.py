"""add owner_id to resume_versions

Revision ID: df1f114b5aee
Revises: 0900f3514ad2
Create Date: 2026-09-04 11:17:45.301760

Schema groundwork only for a future multi-user direction that has been
discussed but not committed to - see huntloop-architecture-decisions.md.
Nullable, no default, and deliberately no ForeignKey yet (there is no
users table to reference). Nothing in the application reads or writes
this column yet.

Hand-written rather than the raw `alembic revision --autogenerate`
output: autogenerate also picked up a batch of unrelated pre-existing
schema drift (job_postings/job_sources/job_skills/job_locations type and
constraint diffs between the live DB and the current models, plus a
Vector-type comparison quirk) that isn't part of this change and whose
generated code doesn't even import cleanly (undefined `pgvector`/
`huntloop` names) - trimmed down to just the one real, intended column
addition.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'df1f114b5aee'
down_revision: Union[str, Sequence[str], None] = '0900f3514ad2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('resume_versions', sa.Column('owner_id', sa.Integer(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('resume_versions', 'owner_id')
