"""add location normalization columns to job_locations

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-09-07

Additive canonical-grouping layer over the free-text
job_locations.location_name (see huntloop.location_normalization).
location_name itself is untouched. Guarded with inspect() the same way
f3a7c9d21b44 was, in case a pre-Alembic create_all() ever left one of
these columns on a dev DB.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUMNS = [
    ("location_city", sa.String(length=120)),
    ("location_region", sa.String(length=64)),
    ("location_country", sa.String(length=80)),
    ("location_is_remote", sa.Boolean()),
    ("location_canonical", sa.String(length=255)),
]


def upgrade() -> None:
    bind = op.get_bind()
    existing = {c["name"] for c in sa.inspect(bind).get_columns("job_locations")}
    for name, type_ in _COLUMNS:
        if name not in existing:
            op.add_column("job_locations", sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    existing = {c["name"] for c in sa.inspect(bind).get_columns("job_locations")}
    for name, _ in reversed(_COLUMNS):
        if name in existing:
            op.drop_column("job_locations", name)
