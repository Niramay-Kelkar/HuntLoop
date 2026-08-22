"""add embedding vector columns to resume_versions and job_postings

Revision ID: 08af7f0a020c
Revises: cbf7cee7fb12
Create Date: 2026-08-22 13:18:04.006118

Adds a nullable `embedding vector(384)` column to both `resume_versions`
and `job_postings` (384 = all-MiniLM-L6-v2's actual output dimension,
confirmed against the model's own published
1_Pooling/config.json ("word_embedding_dimension": 384), not assumed).
Requires the `vector` extension already enabled (c2d25907fe8e). Column-
only - populating them is scripts/backfill_job_embeddings.py and
scripts/ingest_resume.py, not this migration.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector


# revision identifiers, used by Alembic.
revision: str = '08af7f0a020c'
down_revision: Union[str, Sequence[str], None] = 'cbf7cee7fb12'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

EMBEDDING_DIM = 384


def upgrade() -> None:
    op.add_column('resume_versions', sa.Column('embedding', Vector(EMBEDDING_DIM), nullable=True))
    op.add_column('job_postings', sa.Column('embedding', Vector(EMBEDDING_DIM), nullable=True))


def downgrade() -> None:
    op.drop_column('job_postings', 'embedding')
    op.drop_column('resume_versions', 'embedding')
