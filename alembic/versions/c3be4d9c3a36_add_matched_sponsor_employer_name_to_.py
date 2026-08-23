"""add matched_sponsor_employer_name to companies

Revision ID: c3be4d9c3a36
Revises: 7d31cf7fed9c
Create Date: 2026-08-23 09:42:33.312327

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c3be4d9c3a36'
down_revision: Union[str, Sequence[str], None] = '7d31cf7fed9c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Note: autogenerate also picked up unrelated pre-existing drift between
    # this DB and db_models.py (job_postings/job_skills/job_sources column
    # type/nullability changes, an FK recreation, a couple of dropped
    # columns) - none of that is part of this change, so it's intentionally
    # left out of this migration, same as a89474d578f7 before it.
    op.add_column('companies', sa.Column('matched_sponsor_employer_name', sa.String(length=255), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('companies', 'matched_sponsor_employer_name')
