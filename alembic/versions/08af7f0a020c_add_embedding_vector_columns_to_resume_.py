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

Uses a locally-defined schema-qualified Vector (emits `public.VECTOR(n)`
in DDL) instead of the raw `pgvector.sqlalchemy.Vector` (emits bare
`VECTOR(n)`, which only resolves when `public` is on the connecting
role/database's `search_path` - true by Postgres's own default, but not
guaranteed) - same fix `huntloop.db_models.Vector` already applies to
every application-facing pgvector column, applied here directly rather
than imported so this migration stays self-contained (migrations here
don't import app code). This changes only how the column type is
*declared* in this file, not the DDL a normal `search_path` produces:
`public.VECTOR(384)` and `VECTOR(384)` resolve to the exact same
Postgres type when `public` is on the path (Postgres's default), so
every database that already ran this migration is unaffected - this is
not a new revision.
"""
from typing import Any, Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector as _Vector


class Vector(_Vector):
    """Same as pgvector.sqlalchemy.Vector, but always emits a schema-
    qualified `public.VECTOR(n)` in DDL instead of the bare `VECTOR(n)`
    the base class emits - see huntloop.db_models.Vector, which this
    mirrors (DDL-only override; value bind/result processing are
    inherited unchanged)."""

    cache_ok = True

    def get_col_spec(self, **kw: Any) -> str:
        return f"public.{super().get_col_spec(**kw)}"


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
