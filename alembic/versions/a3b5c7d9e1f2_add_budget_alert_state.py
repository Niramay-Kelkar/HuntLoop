"""add budget_alert_state table

Revision ID: a3b5c7d9e1f2
Revises: f1a2b3c4d5e6
Create Date: 2026-10-09 00:00:00.000000

Adds the shared last-alerted-threshold tracker backing
huntloop.budget_alerts (Resend Phase B - budget-threshold email alerts,
see CLAUDE.md/SESSIONS.md). One row per (ledger, period) - `ledger` names
which usage ledger this is ("tavily" / "modal" / "triage" today, any
future one later), `period` is that ledger's current reset window
("YYYY-MM" for the two monthly ledgers, "YYYY-MM-DD" for triage's daily
one). A single shared table, rather than a `last_alerted_threshold`
column bolted onto tavily_usage/modal_usage separately (and a new
ledger-shaped table invented just to hold triage's counter, which has no
dedicated ledger row at all today) - see huntloop.budget_alerts' module
docstring for the full reasoning.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3b5c7d9e1f2'
down_revision: Union[str, Sequence[str], None] = 'f1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "budget_alert_state",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("ledger", sa.String(length=50), nullable=False),
        sa.Column("period", sa.String(length=10), nullable=False),
        sa.Column("last_threshold", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_unique_constraint(
        "uq_budget_alert_state_ledger_period", "budget_alert_state", ["ledger", "period"]
    )


def downgrade() -> None:
    op.drop_table("budget_alert_state")
