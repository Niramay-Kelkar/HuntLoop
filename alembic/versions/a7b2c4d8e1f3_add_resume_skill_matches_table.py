"""add resume_skill_matches table

Revision ID: a7b2c4d8e1f3
Revises: c1d2e3f4a5b6
Create Date: 2026-10-08 12:10:00.000000

Adds resume_skill_matches, keyed by (job_posting_id, resume_version_id),
to hold the matched_skills/missing_skills pair per resume version a job
has been scored against. Purely additive - job_postings.matched_skills/
missing_skills are left in place untouched in this migration; they are
only deprecated in a later, separate step once every read/write path has
moved over to this table.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7b2c4d8e1f3'
down_revision: Union[str, Sequence[str], None] = 'c1d2e3f4a5b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "resume_skill_matches",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("job_posting_id", sa.Integer(), sa.ForeignKey("job_postings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("resume_version_id", sa.Integer(), sa.ForeignKey("resume_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("matched_skills", sa.JSON(), nullable=True),
        sa.Column("missing_skills", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_unique_constraint(
        "uq_resume_skill_matches_job_resume",
        "resume_skill_matches",
        ["job_posting_id", "resume_version_id"],
    )
    op.create_index(
        "ix_resume_skill_matches_resume_version_id",
        "resume_skill_matches",
        ["resume_version_id"],
    )


def downgrade() -> None:
    op.drop_table("resume_skill_matches")
