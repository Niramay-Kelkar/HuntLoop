"""add job_applications table

Revision ID: 7d31cf7fed9c
Revises: 0fdafe5d162e
Create Date: 2026-08-22 22:48:10.046132

Adds `job_applications` (see huntloop.db_models.JobApplication) -
tracks the user's application status per job posting, separate from
job_postings itself. One row per job_posting_id at most
(job_posting_id UNIQUE); a job with no row is treated as not_applied by
the API rather than requiring a row to exist. `status` is a real
Postgres enum (application_status), not a free-text column, matching
the task's explicit ask.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7d31cf7fed9c'
down_revision: Union[str, Sequence[str], None] = '0fdafe5d162e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_application_status = sa.Enum(
    "not_applied", "applied", "interviewing", "rejected", "offer",
    name="application_status",
)


def upgrade() -> None:
    # No separate _application_status.create() call here - op.create_table()
    # already issues CREATE TYPE for an Enum column as part of creating
    # the table; calling .create() first as well raises DuplicateObject.
    op.create_table(
        'job_applications',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('job_posting_id', sa.Integer(), nullable=False),
        sa.Column('status', _application_status, server_default='not_applied', nullable=False),
        sa.Column('applied_at', sa.DateTime(), nullable=True),
        sa.Column(
            'status_updated_at', sa.DateTime(),
            server_default=sa.text('now()'), nullable=False,
        ),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['job_posting_id'], ['job_postings.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('job_posting_id'),
    )


def downgrade() -> None:
    op.drop_table('job_applications')
    _application_status.drop(op.get_bind(), checkfirst=True)
