"""add demo mode tables

Revision ID: a7c9e1f2b3d4
Revises: d4e5f6a7b8c9
Create Date: 2026-10-01 00:00:00.000000

Adds three tables used only when DEMO_MODE is on (see huntloop.demo_mode,
scripts/build_demo_dataset.py, huntloop.api.sponsor_summary). All three
are empty on a normal, non-demo database, so this migration has no
effect on existing behavior.

sponsor_fiscal_year_aggregates and sponsor_overall_aggregates replace
the raw lca_disclosures table a demo deployment never copies, so a demo
API can still serve real sponsorship figures without shipping 1.4M rows
of DOL filings. demo_meta holds the single snapshot date row GET
/demo-info reads for the frontend banner.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7c9e1f2b3d4'
down_revision: Union[str, Sequence[str], None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sponsor_fiscal_year_aggregates",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("employer_name_normalized", sa.String(255), nullable=False),
        sa.Column("fiscal_year", sa.Integer(), nullable=False),
        sa.Column("total_filings", sa.Integer(), nullable=False),
        sa.Column("distinct_titles", sa.Integer(), nullable=False),
        sa.Column("median_wage", sa.Numeric(), nullable=True),
        sa.UniqueConstraint(
            "employer_name_normalized", "fiscal_year", name="sponsor_fiscal_year_aggregates_employer_year_key"
        ),
    )
    op.create_index(
        "ix_sponsor_fiscal_year_aggregates_employer_name_normalized",
        "sponsor_fiscal_year_aggregates",
        ["employer_name_normalized"],
    )

    op.create_table(
        "sponsor_overall_aggregates",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("employer_name_normalized", sa.String(255), nullable=False, unique=True),
        sa.Column("most_recent_fiscal_year", sa.Integer(), nullable=False),
        sa.Column("total_lcas_most_recent_fiscal_year", sa.Integer(), nullable=False),
        sa.Column("median_wage", sa.Numeric(), nullable=True),
        sa.Column("most_frequent_job_title", sa.String(300), nullable=True),
        sa.Column("latest_case_status", sa.String(50), nullable=True),
    )
    op.create_index(
        "ix_sponsor_overall_aggregates_employer_name_normalized",
        "sponsor_overall_aggregates",
        ["employer_name_normalized"],
        unique=True,
    )

    op.create_table(
        "demo_meta",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("built_at", sa.DateTime(), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("demo_meta")
    op.drop_index(
        "ix_sponsor_overall_aggregates_employer_name_normalized", table_name="sponsor_overall_aggregates"
    )
    op.drop_table("sponsor_overall_aggregates")
    op.drop_index(
        "ix_sponsor_fiscal_year_aggregates_employer_name_normalized", table_name="sponsor_fiscal_year_aggregates"
    )
    op.drop_table("sponsor_fiscal_year_aggregates")
