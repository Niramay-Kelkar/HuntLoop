"""add sponsor_name_overrides table

Revision ID: a89474d578f7
Revises: e4c81707e5e3
Create Date: 2026-08-20 18:02:05.306803

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a89474d578f7'
down_revision: Union[str, Sequence[str], None] = 'e4c81707e5e3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Note: autogenerate also picked up unrelated pre-existing drift between
    # this DB and db_models.py (job_postings/job_skills/job_sources column
    # type/nullability changes, an FK recreation, a couple of dropped
    # columns) - none of that is part of this change, so it's intentionally
    # left out of this migration.
    op.create_table('sponsor_name_overrides',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('raw_company_name', sa.String(length=255), nullable=False),
    sa.Column('employer_name_normalized', sa.String(length=255), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_sponsor_name_overrides_raw_company_name'), 'sponsor_name_overrides', ['raw_company_name'], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_sponsor_name_overrides_raw_company_name'), table_name='sponsor_name_overrides')
    op.drop_table('sponsor_name_overrides')
