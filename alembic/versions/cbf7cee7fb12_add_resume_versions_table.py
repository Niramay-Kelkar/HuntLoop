"""add resume versions table

Revision ID: cbf7cee7fb12
Revises: c2d25907fe8e
Create Date: 2026-08-22 12:48:22.833955

Adds `resume_versions` (see huntloop.db_models.ResumeVersion,
scripts/ingest_resume.py) - ingested resume text, versioned so a resume
update never overwrites history. `is_active` marks the single version to
match against; ingestion is the only thing this step adds - no
embeddings, matching, or skills-extraction logic.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'cbf7cee7fb12'
down_revision: Union[str, Sequence[str], None] = 'c2d25907fe8e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'resume_versions',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('version_number', sa.Integer(), nullable=False),
        sa.Column('uploaded_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
        sa.Column('file_path', sa.String(length=500), nullable=False),
        sa.Column('extracted_text', sa.Text(), nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default='false', nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('version_number'),
    )


def downgrade() -> None:
    op.drop_table('resume_versions')
