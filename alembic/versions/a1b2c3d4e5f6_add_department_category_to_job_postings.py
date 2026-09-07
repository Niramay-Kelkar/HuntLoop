"""add department_category to job_postings

Revision ID: a1b2c3d4e5f6
Revises: f3a7c9d21b44
Create Date: 2026-09-07 10:00:00.000000

Adds a nullable `department_category` column to `job_postings` - the
canonical, controlled-vocabulary category that the raw scraped
`department` free-text string is mapped onto (see
huntloop.department_categorization). One of ~18 canonical categories
(Engineering, Sales, Healthcare & Clinical, ...) or "Other".

This is an ADDITIVE categorization layer: the original `department`
value is kept intact and unchanged. `department_category` is nullable
because `department` itself is NULL for ~44% of postings (100% of
Workday's, by source-data design) and a posting with no raw department
has nothing to categorize.

This migration only adds the column; huntloop.pipelines populates it at
insert time for new postings and scripts/backfill_department_category.py
fills it for existing rows.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'f3a7c9d21b44'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col['name'] for col in inspector.get_columns('job_postings')}
    if 'department_category' not in columns:
        op.add_column(
            'job_postings',
            sa.Column('department_category', sa.String(length=50), nullable=True),
        )


def downgrade() -> None:
    op.drop_column('job_postings', 'department_category')
