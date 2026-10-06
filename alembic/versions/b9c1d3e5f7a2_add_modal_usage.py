"""add modal_usage table

Revision ID: b9c1d3e5f7a2
Revises: a7c4e9f2d8b3
Create Date: 2026-10-06 00:00:00.000000

Adds the monthly invocation-count ledger backing
huntloop.modal_resume_processing (resume parsing + embedding generation
on Modal - see CLAUDE.md). Modeled directly on tavily_usage, added by
e4f8a2c9d7b1 - one row per calendar month, so the monthly invocation
budget check survives the API process restarting and stays correct
across concurrent requests.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b9c1d3e5f7a2'
down_revision: Union[str, Sequence[str], None] = 'a7c4e9f2d8b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "modal_usage",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("month", sa.String(length=7), nullable=False),
        sa.Column("invocations_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_unique_constraint("uq_modal_usage_month", "modal_usage", ["month"])


def downgrade() -> None:
    op.drop_table("modal_usage")
