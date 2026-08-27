"""Create raw act tables.

Revision ID: d1a7e3c92f14
Revises: c9f2b5d81e03
Create Date: 2026-08-27

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "d1a7e3c92f14"
down_revision: str | Sequence[str] | None = "c9f2b5d81e03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "raw_acts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slug", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.UniqueConstraint("slug", name="uq_raw_acts_slug"),
    )
    op.create_index("ix_raw_acts_status", "raw_acts", ["status"])
    op.create_table(
        "raw_act_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("raw_act_id", sa.Integer(), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("fetch_status", sa.String(length=32), nullable=False),
        sa.Column("fetch_error", sa.Text(), nullable=True),
        sa.Column("html", sa.Text(), nullable=True),
        sa.Column("source_metadata", JSONB(), nullable=True),
        sa.Column("parse_status", sa.String(length=32), nullable=False),
        sa.Column("expected_provision_count", sa.Integer(), nullable=True),
        sa.Column("extracted_provision_count", sa.Integer(), nullable=True),
        sa.Column("needs_review", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["raw_act_id"],
            ["raw_acts.id"],
            name="fk_raw_act_versions_raw_act_id_raw_acts",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "raw_act_id",
            "valid_from",
            name="uq_raw_act_versions_raw_act_id_valid_from",
        ),
    )
    op.create_index("ix_raw_act_versions_raw_act_id", "raw_act_versions", ["raw_act_id"])
    op.create_index("ix_raw_act_versions_valid_from", "raw_act_versions", ["valid_from"])


def downgrade() -> None:
    op.drop_table("raw_act_versions")
    op.drop_table("raw_acts")
