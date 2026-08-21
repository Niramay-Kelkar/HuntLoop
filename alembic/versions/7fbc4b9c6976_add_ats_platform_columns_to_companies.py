"""add ats platform columns to companies

Revision ID: 7fbc4b9c6976
Revises: 3620e2fbbd47
Create Date: 2026-08-21 12:28:55.616166

Adds three nullable columns to `companies` for the ATS-detection result
(see detect_ats(), src/huntloop/ats_detection.py, 2026-08-21):
`ats_platform` (e.g. "greenhouse", "lever", or "unknown"), `ats_token`
(the extracted token/company-slug, if any), and `careers_url` (the URL
detect_ats() was run against). Nullable because not every company has a
detected value yet - this migration only adds the columns, it doesn't
backfill any data.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7fbc4b9c6976'
down_revision: Union[str, Sequence[str], None] = '3620e2fbbd47'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('companies', sa.Column('ats_platform', sa.String(length=50), nullable=True))
    op.add_column('companies', sa.Column('ats_token', sa.String(length=255), nullable=True))
    op.add_column('companies', sa.Column('careers_url', sa.String(length=500), nullable=True))


def downgrade() -> None:
    op.drop_column('companies', 'careers_url')
    op.drop_column('companies', 'ats_token')
    op.drop_column('companies', 'ats_platform')
