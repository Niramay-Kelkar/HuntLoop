"""add display_name to companies

Revision ID: a7c4e9f2d8b3
Revises: e4f8a2c9d7b1
Create Date: 2026-10-04 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a7c4e9f2d8b3'
down_revision: Union[str, Sequence[str], None] = 'e4f8a2c9d7b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Purely additive - companies.name (the lowercase ATS slug) stays the
    # dedup/sync/filter key everywhere; this is just a human-readable label
    # to show instead of it, populated separately (see
    # scripts/backfill_company_display_names.py). No backfill here.
    op.add_column('companies', sa.Column('display_name', sa.String(length=255), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('companies', 'display_name')
