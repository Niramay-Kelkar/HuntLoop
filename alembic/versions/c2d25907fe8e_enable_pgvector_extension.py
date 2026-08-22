"""enable pgvector extension

Revision ID: c2d25907fe8e
Revises: 7fbc4b9c6976
Create Date: 2026-08-22 11:43:17.676931

Enables the `vector` extension (pgvector) so `vector` columns/operators
become available in this database. Infra only, per this step's scope -
no vector columns, embeddings, or matching logic are added here; this
just proves the extension is installed and enabled ahead of building
anything on top of it.

Requires the pgvector extension's shared library to already be present
on the Postgres server this runs against - true for the `db` service
after docker-compose.yml switched it to `pgvector/pgvector:pg18` (see
that change), and true for local system Postgres only if pgvector has
been installed there separately (see SESSIONS.md/CLAUDE.md for whether
that's been done - as of this migration, it has not).
`CREATE EXTENSION IF NOT EXISTS` is idempotent, so re-running this
migration (or running it against a database where the extension was
already enabled by hand) is a no-op, not an error.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'c2d25907fe8e'
down_revision: Union[str, Sequence[str], None] = '7fbc4b9c6976'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")


def downgrade() -> None:
    op.execute("DROP EXTENSION IF EXISTS vector;")
