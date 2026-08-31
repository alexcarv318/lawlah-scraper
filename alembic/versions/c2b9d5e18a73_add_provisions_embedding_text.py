"""Add provisions.embedding_text.

Revision ID: c2b9d5e18a73
Revises: f7d3a1b84e20
Create Date: 2026-08-31

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c2b9d5e18a73"
down_revision: str | Sequence[str] | None = "f7d3a1b84e20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("provisions", sa.Column("embedding_text", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("provisions", "embedding_text")
