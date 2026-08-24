"""add is_relevant to job_postings

Revision ID: 0900f3514ad2
Revises: c3be4d9c3a36
Create Date: 2026-08-24 10:07:48.249807

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0900f3514ad2'
down_revision: Union[str, Sequence[str], None] = 'c3be4d9c3a36'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Note: autogenerate also picked up unrelated pre-existing drift between
    # this DB and db_models.py (job_locations/job_postings/job_skills/
    # job_sources/resume_versions column type/nullability changes, FK
    # recreations, a couple of dropped columns) - none of that is part of
    # this change, so it's intentionally left out of this migration, same
    # as c3be4d9c3a36 and a89474d578f7 before it.
    op.add_column('job_postings', sa.Column('is_relevant', sa.Boolean(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('job_postings', 'is_relevant')
