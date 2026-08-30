"""Require raw subsidiary legislation to belong to an act.

Revision ID: f8c4d2e91a15
Revises: e4b2c8a91d36
Create Date: 2026-08-30

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f8c4d2e91a15"
down_revision: str | Sequence[str] | None = "e4b2c8a91d36"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "fk_raw_subsidiary_legislations_raw_act_id_raw_acts",
        "raw_subsidiary_legislations",
        type_="foreignkey",
    )
    op.alter_column(
        "raw_subsidiary_legislations",
        "raw_act_id",
        existing_type=sa.Integer(),
        existing_nullable=True,
        nullable=False,
    )
    op.create_foreign_key(
        "fk_raw_subsidiary_legislations_raw_act_id_raw_acts",
        "raw_subsidiary_legislations",
        "raw_acts",
        ["raw_act_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_raw_subsidiary_legislations_raw_act_id_raw_acts",
        "raw_subsidiary_legislations",
        type_="foreignkey",
    )
    op.alter_column(
        "raw_subsidiary_legislations",
        "raw_act_id",
        existing_type=sa.Integer(),
        existing_nullable=False,
        nullable=True,
    )
    op.create_foreign_key(
        "fk_raw_subsidiary_legislations_raw_act_id_raw_acts",
        "raw_subsidiary_legislations",
        "raw_acts",
        ["raw_act_id"],
        ["id"],
        ondelete="SET NULL",
    )
