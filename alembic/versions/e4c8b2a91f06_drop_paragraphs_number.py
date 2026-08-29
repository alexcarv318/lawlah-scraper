"""Drop paragraphs.number.

Revision ID: e4c8b2a91f06
Revises: b8e1a4c70d12
Create Date: 2026-08-28

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e4c8b2a91f06"
down_revision: str | Sequence[str] | None = "b8e1a4c70d12"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("paragraphs", "number", if_exists=True)


def downgrade() -> None:
    op.add_column("paragraphs", sa.Column("number", sa.String(), nullable=True))
