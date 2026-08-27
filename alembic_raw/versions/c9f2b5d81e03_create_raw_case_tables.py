"""Create raw case tables.

Revision ID: c9f2b5d81e03
Revises:
Create Date: 2026-08-27

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "c9f2b5d81e03"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "raw_cases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("neutral_citation", sa.String(), nullable=False),
        sa.Column("date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("search_result", JSONB(), nullable=True),
        sa.UniqueConstraint("neutral_citation", name="uq_raw_cases_neutral_citation"),
    )
    op.create_index("ix_raw_cases_date", "raw_cases", ["date"])
    op.create_index("ix_raw_cases_status", "raw_cases", ["status"])
    op.create_table(
        "raw_case_documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("raw_case_id", sa.Integer(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("fetch_status", sa.String(length=32), nullable=False),
        sa.Column("fetch_error", sa.Text(), nullable=True),
        sa.Column("html", sa.Text(), nullable=True),
        sa.Column("source_metadata", JSONB(), nullable=True),
        sa.Column("layout", sa.String(length=32), nullable=True),
        sa.Column("parse_status", sa.String(length=32), nullable=False),
        sa.Column("expected_paragraph_count", sa.Integer(), nullable=True),
        sa.Column("extracted_paragraph_count", sa.Integer(), nullable=True),
        sa.Column("needs_review", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["raw_case_id"],
            ["raw_cases.id"],
            name="fk_raw_case_documents_raw_case_id_raw_cases",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("raw_case_id", name="uq_raw_case_documents_raw_case_id"),
    )


def downgrade() -> None:
    op.drop_table("raw_case_documents")
    op.drop_table("raw_cases")
