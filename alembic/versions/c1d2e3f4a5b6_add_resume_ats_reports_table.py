"""add resume_ats_reports table

Revision ID: c1d2e3f4a5b6
Revises: b9c1d3e5f7a2
Create Date: 2026-10-08 00:00:00.000000

Adds a cache table for huntloop.resume_ats_report / GET
/resumes/active/ats-report: one row per resume_versions.id, holding an
LLM-generated ATS-compatibility score plus structured feedback
(keyword/wording/formatting). Computed once per resume version and
served from cache on every later request - see that module's docstring
for why. Purely additive - no existing table is touched, and in
particular nothing here reads from or writes to matched_skills/
missing_skills or anything else the skill-gap backfill job depends on.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c1d2e3f4a5b6'
down_revision: Union[str, Sequence[str], None] = 'b9c1d3e5f7a2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "resume_ats_reports",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("resume_version_id", sa.Integer(), sa.ForeignKey("resume_versions.id"), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("keyword_feedback", sa.JSON(), nullable=False),
        sa.Column("wording_feedback", sa.JSON(), nullable=False),
        sa.Column("formatting_feedback", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_unique_constraint(
        "uq_resume_ats_reports_resume_version_id", "resume_ats_reports", ["resume_version_id"]
    )


def downgrade() -> None:
    op.drop_table("resume_ats_reports")
