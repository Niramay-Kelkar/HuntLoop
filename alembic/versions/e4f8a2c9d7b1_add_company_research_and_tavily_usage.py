"""add company_research and tavily_usage tables

Revision ID: e4f8a2c9d7b1
Revises: b7e2a5c9f1d3
Create Date: 2026-10-02 00:00:00.000000

Adds the two tables backing the new company-research enrichment layer
(huntloop.company_research, scripts/backfill_company_research.py - see
CLAUDE.md). This is a standalone integration, unrelated to the
feedback/triage pipeline the prior migration added.

`company_research` holds one Tavily-sourced research snapshot per
company (`company_id` unique - a refresh replaces the row, it is not a
history table). `tavily_usage` is a durable monthly request-count ledger
(one row per calendar month) so the monthly credit budget check survives
the backfill script being re-run or the process restarting.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e4f8a2c9d7b1'
down_revision: Union[str, Sequence[str], None] = 'b7e2a5c9f1d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "company_research",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("recent_news", sa.JSON(), nullable=True),
        sa.Column("funding_signal", sa.Text(), nullable=True),
        sa.Column("hiring_signal", sa.Text(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_unique_constraint("uq_company_research_company_id", "company_research", ["company_id"])

    op.create_table(
        "tavily_usage",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("month", sa.String(length=7), nullable=False),
        sa.Column("requests_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_unique_constraint("uq_tavily_usage_month", "tavily_usage", ["month"])


def downgrade() -> None:
    op.drop_table("tavily_usage")
    op.drop_table("company_research")
