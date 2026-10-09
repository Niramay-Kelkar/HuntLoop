"""add resend_usage table

Revision ID: f1a2b3c4d5e6
Revises: a7b2c4d8e1f3
Create Date: 2026-10-09 00:00:00.000000

Adds the send-count ledger backing huntloop.resend_client (Resend
email-alert infrastructure - see CLAUDE.md/SESSIONS.md). One row per
calendar DAY (not per month like tavily_usage/modal_usage), because
Resend's free tier caps at 100 emails/day - a tighter constraint than
its 3,000/month cap - so the daily count has to be its own durable
counter, not just derived in memory. The monthly cap is enforced by
summing this same table's rows for the current month, so there is only
ever one ledger table to keep consistent, not two that could drift
apart.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, Sequence[str], None] = 'a7b2c4d8e1f3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "resend_usage",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("day", sa.String(length=10), nullable=False),
        sa.Column("emails_sent", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_unique_constraint("uq_resend_usage_day", "resend_usage", ["day"])


def downgrade() -> None:
    op.drop_table("resend_usage")
