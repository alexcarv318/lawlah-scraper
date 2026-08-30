"""Add promoted flags on raw legislation versions.

Revision ID: a3e7f1c04b28
Revises: f8c4d2e91a15
Create Date: 2026-08-30

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a3e7f1c04b28"
down_revision: str | Sequence[str] | None = "f8c4d2e91a15"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "raw_act_versions",
        sa.Column("promoted", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column("raw_act_versions", "promoted", server_default=None)
    op.add_column(
        "raw_subsidiary_legislation_versions",
        sa.Column("promoted", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column("raw_subsidiary_legislation_versions", "promoted", server_default=None)


def downgrade() -> None:
    op.drop_column("raw_subsidiary_legislation_versions", "promoted")
    op.drop_column("raw_act_versions", "promoted")
