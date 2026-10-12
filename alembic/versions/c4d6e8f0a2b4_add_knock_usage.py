"""add knock_usage table

Revision ID: c4d6e8f0a2b4
Revises: a3b5c7d9e1f2
Create Date: 2026-10-12 00:00:00.000000

Adds the trigger-count ledger backing huntloop.knock_client (Knock
notification-orchestration layer in front of Resend - see
CLAUDE.md/SESSIONS.md). One row per calendar DAY, mirroring
resend_usage - Knock's free-tier workflow-trigger budget is a daily
figure, the tighter of its two caps, so the daily count needs its own
durable row, not just something derived in memory. The monthly cap is
enforced by summing this same table's rows for the current month, so
there is only ever one ledger table to keep consistent.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4d6e8f0a2b4'
down_revision: Union[str, Sequence[str], None] = 'a3b5c7d9e1f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "knock_usage",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("day", sa.String(length=10), nullable=False),
        sa.Column("triggers_sent", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_unique_constraint("uq_knock_usage_day", "knock_usage", ["day"])


def downgrade() -> None:
    op.drop_table("knock_usage")
