"""Add identify columns to references.

Revision ID: d4e8f1a62b90
Revises: c2b9d5e18a73
Create Date: 2026-08-31

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY

from alembic import op

revision: str = "d4e8f1a62b90"
down_revision: str | Sequence[str] | None = "c2b9d5e18a73"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("references", sa.Column("kind", sa.String(length=32), nullable=True))
    op.add_column("references", sa.Column("alias_short_name", sa.String(), nullable=True))
    op.add_column("references", sa.Column("title", sa.String(), nullable=True))
    op.add_column("references", sa.Column("neutral_citation", sa.String(), nullable=True))
    op.add_column("references", sa.Column("slr_citation", sa.String(), nullable=True))
    op.add_column("references", sa.Column("edition", sa.String(), nullable=True))
    op.add_column(
        "references",
        sa.Column("provision_citations", ARRAY(sa.String()), nullable=False, server_default="{}"),
    )
    op.add_column(
        "references",
        sa.Column("paragraph_pins", ARRAY(sa.Integer()), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("references", "paragraph_pins")
    op.drop_column("references", "provision_citations")
    op.drop_column("references", "edition")
    op.drop_column("references", "slr_citation")
    op.drop_column("references", "neutral_citation")
    op.drop_column("references", "title")
    op.drop_column("references", "alias_short_name")
    op.drop_column("references", "kind")
