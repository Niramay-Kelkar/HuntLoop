"""add feedback table

Revision ID: b7e2a5c9f1d3
Revises: a7c9e1f2b3d4
Create Date: 2026-10-01 00:00:00.000000

Adds the `feedback` table backing the new feedback capture/triage
pipeline (POST /feedback, GET /feedback/public, scripts/triage_feedback.py,
scripts/review_feedback.py - see huntloop.db_models.Feedback and
CLAUDE.md). `user_id` is nullable with no FK - there is no users table
yet; this column only exists so a future auth migration can backfill it
without a schema change.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7e2a5c9f1d3'
down_revision: Union[str, Sequence[str], None] = 'a7c9e1f2b3d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    feedback_category = sa.Enum(
        "bug", "feature", "question", "other", name="feedback_category"
    )
    feedback_triage_status = sa.Enum(
        "pending", "done", "skipped_budget", name="feedback_triage_status"
    )
    feedback_status = sa.Enum(
        "open", "in_progress", "resolved", "wont_fix", name="feedback_status"
    )

    op.create_table(
        "feedback",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("category", feedback_category, nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("context", sa.JSON(), nullable=True),
        sa.Column("llm_summary", sa.Text(), nullable=True),
        sa.Column(
            "triage_status", feedback_triage_status, nullable=False, server_default="pending"
        ),
        sa.Column("status", feedback_status, nullable=False, server_default="open"),
        sa.Column("is_public", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("ip_hash", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_feedback_ip_hash", "feedback", ["ip_hash"])
    op.create_index("ix_feedback_created_at", "feedback", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_feedback_created_at", table_name="feedback")
    op.drop_index("ix_feedback_ip_hash", table_name="feedback")
    op.drop_table("feedback")
    sa.Enum(name="feedback_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="feedback_triage_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="feedback_category").drop(op.get_bind(), checkfirst=True)
