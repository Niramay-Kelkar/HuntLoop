"""add sponsor_checked_at to companies

Revision ID: d4e5f6a7b8c9
Revises: b2c3d4e5f6a7
Create Date: 2026-09-12 00:00:00.000000

Adds a nullable `sponsor_checked_at` timestamp to `companies`, set by
scripts/resolve_sponsor_matches.py every time it processes a company
(whether or not a match cleared the threshold) - see CLAUDE.md's
sponsor-matching architectural decisions.

Why this is needed: `companies.matched_sponsor_employer_name` being NULL
is ambiguous between "we looked and found no sponsor history" and "we
have never run the matcher against this company at all" -
resolve_sponsor_matches.py has only ever been run once, against the
original 9-company set (confirmed live: those 9 rows' `updated_at` still
equals their `created_at` from that run; the other 734 companies onboarded
since then have never been checked). A page-scoped assistant answering
"does this company sponsor visas?" needs to distinguish those two cases
honestly rather than reporting "no" when the truth is "not checked yet".

Nullable: NULL means never checked, matching every existing company row's
real state until resolve_sponsor_matches.py is (re-)run.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, Sequence[str], None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('companies', sa.Column('sponsor_checked_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column('companies', 'sponsor_checked_at')
