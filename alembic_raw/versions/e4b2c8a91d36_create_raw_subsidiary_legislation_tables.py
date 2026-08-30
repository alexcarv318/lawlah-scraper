"""Create raw subsidiary legislation tables.

Revision ID: e4b2c8a91d36
Revises: f6a1c4d82b07
Create Date: 2026-08-30

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "e4b2c8a91d36"
down_revision: str | Sequence[str] | None = "f6a1c4d82b07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_raw_act_versions_fetch_status",
        "raw_act_versions",
        ["fetch_status"],
    )
    op.create_table(
        "raw_subsidiary_legislations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("raw_act_id", sa.Integer(), nullable=True),
        sa.Column("slug", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("number", sa.String(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(
            ["raw_act_id"],
            ["raw_acts.id"],
            name="fk_raw_subsidiary_legislations_raw_act_id_raw_acts",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("slug", name="uq_raw_subsidiary_legislations_slug"),
    )
    op.create_index(
        "ix_raw_subsidiary_legislations_raw_act_id",
        "raw_subsidiary_legislations",
        ["raw_act_id"],
    )
    op.create_index(
        "ix_raw_subsidiary_legislations_status",
        "raw_subsidiary_legislations",
        ["status"],
    )
    op.create_table(
        "raw_subsidiary_legislation_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("raw_subsidiary_legislation_id", sa.Integer(), nullable=False),
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
            ["raw_subsidiary_legislation_id"],
            ["raw_subsidiary_legislations.id"],
            name="fk_raw_sl_versions_raw_sl_id_raw_subsidiary_legislations",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "raw_subsidiary_legislation_id",
            "valid_from",
            name="uq_raw_subsidiary_legislation_versions_parent_valid_from",
        ),
    )
    op.create_index(
        "ix_raw_sl_versions_parent_id",
        "raw_subsidiary_legislation_versions",
        ["raw_subsidiary_legislation_id"],
    )
    op.create_index(
        "ix_raw_sl_versions_valid_from",
        "raw_subsidiary_legislation_versions",
        ["valid_from"],
    )
    op.create_index(
        "ix_raw_sl_versions_fetch_status",
        "raw_subsidiary_legislation_versions",
        ["fetch_status"],
    )


def downgrade() -> None:
    op.drop_table("raw_subsidiary_legislation_versions")
    op.drop_table("raw_subsidiary_legislations")
    op.drop_index("ix_raw_act_versions_fetch_status", table_name="raw_act_versions")
