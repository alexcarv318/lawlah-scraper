"""Add raw_case_documents.promoted.

Revision ID: f6a1c4d82b07
Revises: d1a7e3c92f14
Create Date: 2026-08-28

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f6a1c4d82b07"
down_revision: str | Sequence[str] | None = "d1a7e3c92f14"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "raw_case_documents",
        sa.Column("promoted", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column("raw_case_documents", "promoted", server_default=None)


def downgrade() -> None:
    op.drop_column("raw_case_documents", "promoted")
