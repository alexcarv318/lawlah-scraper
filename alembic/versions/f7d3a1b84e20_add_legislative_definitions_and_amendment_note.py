"""Add legislative definitions and provision amendment notes.

Revision ID: f7d3a1b84e20
Revises: e4c8b2a91f06
Create Date: 2026-08-30

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f7d3a1b84e20"
down_revision: str | Sequence[str] | None = "e4c8b2a91f06"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("provisions", sa.Column("amendment_note", sa.Text(), nullable=True))
    op.create_table(
        "legislative_definitions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("act_id", sa.Integer(), nullable=False),
        sa.Column("term", sa.String(), nullable=False),
        sa.Column("definition", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["act_id"],
            ["acts.id"],
            name="fk_legislative_definitions_act_id_acts",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("act_id", "term", name="uq_legislative_definitions_act_id_term"),
    )
    op.create_index(
        "ix_legislative_definitions_act_id",
        "legislative_definitions",
        ["act_id"],
    )


def downgrade() -> None:
    op.drop_table("legislative_definitions")
    op.drop_column("provisions", "amendment_note")
