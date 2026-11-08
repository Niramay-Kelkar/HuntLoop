"""add resume_deep_dives table

Revision ID: d5e7f9a1b3c6
Revises: c4d6e8f0a2b4
Create Date: 2026-11-07 00:00:00.000000

Adds the table backing the Resume Deep-Dive (see huntloop.deep_dive,
huntloop.modal_deep_dive, huntloop.api.routers.deep_dive): one heavier
LLM skill-gap analysis per (resume_version, job_posting) pair, run
asynchronously on Modal and polled by the frontend.

Purely additive - no existing table or column is touched. The
deep_dive_status Postgres enum uses lowercase labels matching
huntloop.db_models.DeepDiveStatus's .value strings (the model's Enum
column passes values_callable for exactly this reason - see that
column's comment).

`modal_call_id` is a handle only: Modal's own call results expire, so
the finished payload is copied into `result` here on the first
successful poll.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd5e7f9a1b3c6'
down_revision: Union[str, Sequence[str], None] = 'c4d6e8f0a2b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Passed straight into create_table rather than pre-created with
    # .create(checkfirst=True) first - create_table emits its own
    # CREATE TYPE for an Enum column, so pre-creating it makes the
    # second attempt fail with DuplicateObject (hit for real while
    # testing this migration locally). Same shape b7e2a5c9f1d3 uses for
    # the feedback enums.
    deep_dive_status = sa.Enum(
        "pending", "running", "done", "failed", name="deep_dive_status"
    )

    op.create_table(
        "resume_deep_dives",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "resume_version_id",
            sa.Integer(),
            sa.ForeignKey("resume_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "job_posting_id",
            sa.Integer(),
            sa.ForeignKey("job_postings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "status",
            deep_dive_status,
            nullable=False,
            server_default="pending",
        ),
        sa.Column("modal_call_id", sa.String(length=255), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("dispatched_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_unique_constraint(
        "uq_resume_deep_dives_resume_job",
        "resume_deep_dives",
        ["resume_version_id", "job_posting_id"],
    )


def downgrade() -> None:
    op.drop_table("resume_deep_dives")
    sa.Enum(name="deep_dive_status").drop(op.get_bind(), checkfirst=True)
