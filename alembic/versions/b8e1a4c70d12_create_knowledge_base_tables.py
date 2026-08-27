"""Create knowledge base tables.

Revision ID: b8e1a4c70d12
Revises:
Create Date: 2026-08-27

"""

from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

from alembic import op

revision: str = "b8e1a4c70d12"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "courts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.UniqueConstraint("code", name="uq_courts_code"),
    )
    op.create_table(
        "judges",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("full_name", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=True),
    )
    op.create_table(
        "parties",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.UniqueConstraint("name", name="uq_parties_name"),
    )
    op.create_table(
        "counsels",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.UniqueConstraint("name", name="uq_counsels_name"),
    )
    op.create_table(
        "topics",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.UniqueConstraint("name", name="uq_topics_name"),
    )
    op.create_table(
        "concepts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("topic_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["topic_id"],
            ["topics.id"],
            name="fk_concepts_topic_id_topics",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("name", name="uq_concepts_name"),
    )
    op.create_index("ix_concepts_topic_id", "concepts", ["topic_id"])
    op.create_table(
        "functional_roles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("applies_to", sa.String(length=32), nullable=False),
        sa.UniqueConstraint("name", name="uq_functional_roles_name"),
    )
    op.create_table(
        "acts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("uri", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.UniqueConstraint("uri", name="uq_acts_uri"),
    )
    op.create_table(
        "act_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("act_id", sa.Integer(), nullable=False),
        sa.Column("uri", sa.String(), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["act_id"],
            ["acts.id"],
            name="fk_act_versions_act_id_acts",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("uri", name="uq_act_versions_uri"),
        sa.UniqueConstraint(
            "act_id",
            "valid_from",
            name="uq_act_versions_act_id_valid_from",
        ),
    )
    op.create_index("ix_act_versions_act_id", "act_versions", ["act_id"])
    op.create_index(
        "uq_act_versions_current",
        "act_versions",
        ["act_id"],
        unique=True,
        postgresql_where=sa.text("is_current"),
    )
    op.create_table(
        "subsidiary_legislations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("act_id", sa.Integer(), nullable=True),
        sa.Column("uri", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("number", sa.String(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.ForeignKeyConstraint(
            ["act_id"],
            ["acts.id"],
            name="fk_subsidiary_legislations_act_id_acts",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("uri", name="uq_subsidiary_legislations_uri"),
    )
    op.create_index("ix_subsidiary_legislations_act_id", "subsidiary_legislations", ["act_id"])
    op.create_table(
        "cases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("court_id", sa.Integer(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("uri", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("neutral_citation", sa.String(), nullable=False),
        sa.Column("case_number", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(
            ["court_id"],
            ["courts.id"],
            name="fk_cases_court_id_courts",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("uri", name="uq_cases_uri"),
        sa.UniqueConstraint("neutral_citation", name="uq_cases_neutral_citation"),
    )
    op.create_index("ix_cases_court_id", "cases", ["court_id"])
    op.create_index("ix_cases_date", "cases", ["date"])
    op.create_table(
        "case_judges",
        sa.Column("case_id", sa.Integer(), primary_key=True),
        sa.Column("judge_id", sa.Integer(), primary_key=True),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
            name="fk_case_judges_case_id_cases",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["judge_id"],
            ["judges.id"],
            name="fk_case_judges_judge_id_judges",
            ondelete="CASCADE",
        ),
    )
    op.create_table(
        "case_parties",
        sa.Column("case_id", sa.Integer(), primary_key=True),
        sa.Column("party_id", sa.Integer(), primary_key=True),
        sa.Column("role", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
            name="fk_case_parties_case_id_cases",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["party_id"],
            ["parties.id"],
            name="fk_case_parties_party_id_parties",
            ondelete="CASCADE",
        ),
    )
    op.create_table(
        "case_counsels",
        sa.Column("case_id", sa.Integer(), primary_key=True),
        sa.Column("counsel_id", sa.Integer(), primary_key=True),
        sa.Column("represents", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
            name="fk_case_counsels_case_id_cases",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["counsel_id"],
            ["counsels.id"],
            name="fk_case_counsels_counsel_id_counsels",
            ondelete="CASCADE",
        ),
    )
    op.create_table(
        "case_topics",
        sa.Column("case_id", sa.Integer(), primary_key=True),
        sa.Column("topic_id", sa.Integer(), primary_key=True),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
            name="fk_case_topics_case_id_cases",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["topic_id"],
            ["topics.id"],
            name="fk_case_topics_topic_id_topics",
            ondelete="CASCADE",
        ),
    )
    op.create_table(
        "case_concepts",
        sa.Column("case_id", sa.Integer(), primary_key=True),
        sa.Column("concept_id", sa.Integer(), primary_key=True),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
            name="fk_case_concepts_case_id_cases",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["concept_id"],
            ["concepts.id"],
            name="fk_case_concepts_concept_id_concepts",
            ondelete="CASCADE",
        ),
    )
    op.create_table(
        "provisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("act_version_id", sa.Integer(), nullable=True),
        sa.Column("subsidiary_legislation_id", sa.Integer(), nullable=True),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column("functional_role_id", sa.Integer(), nullable=True),
        sa.Column("uri", sa.String(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("citation", sa.String(), nullable=True),
        sa.Column("heading", sa.String(), nullable=True),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("descendant_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("embedding", Vector(1536), nullable=True),
        sa.CheckConstraint(
            "(act_version_id IS NOT NULL AND subsidiary_legislation_id IS NULL)"
            " OR (act_version_id IS NULL AND subsidiary_legislation_id IS NOT NULL)",
            name="parent_document",
        ),
        sa.ForeignKeyConstraint(
            ["act_version_id"],
            ["act_versions.id"],
            name="fk_provisions_act_version_id_act_versions",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["subsidiary_legislation_id"],
            ["subsidiary_legislations.id"],
            name="fk_provisions_subsidiary_legislation_id_subsidiary_legislations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["provisions.id"],
            name="fk_provisions_parent_id_provisions",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["functional_role_id"],
            ["functional_roles.id"],
            name="fk_provisions_functional_role_id_functional_roles",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("uri", name="uq_provisions_uri"),
    )
    op.create_index("ix_provisions_act_version_id", "provisions", ["act_version_id"])
    op.create_index(
        "ix_provisions_subsidiary_legislation_id",
        "provisions",
        ["subsidiary_legislation_id"],
    )
    op.create_index("ix_provisions_parent_id", "provisions", ["parent_id"])
    op.create_table(
        "paragraphs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("case_id", sa.Integer(), nullable=False),
        sa.Column("functional_role_id", sa.Integer(), nullable=True),
        sa.Column("uri", sa.String(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("number", sa.String(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(1536), nullable=True),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
            name="fk_paragraphs_case_id_cases",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["functional_role_id"],
            ["functional_roles.id"],
            name="fk_paragraphs_functional_role_id_functional_roles",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("uri", name="uq_paragraphs_uri"),
    )
    op.create_index("ix_paragraphs_case_id", "paragraphs", ["case_id"])
    op.create_table(
        "paragraph_topics",
        sa.Column("paragraph_id", sa.Integer(), primary_key=True),
        sa.Column("topic_id", sa.Integer(), primary_key=True),
        sa.ForeignKeyConstraint(
            ["paragraph_id"],
            ["paragraphs.id"],
            name="fk_paragraph_topics_paragraph_id_paragraphs",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["topic_id"],
            ["topics.id"],
            name="fk_paragraph_topics_topic_id_topics",
            ondelete="CASCADE",
        ),
    )
    op.create_table(
        "paragraph_concepts",
        sa.Column("paragraph_id", sa.Integer(), primary_key=True),
        sa.Column("concept_id", sa.Integer(), primary_key=True),
        sa.ForeignKeyConstraint(
            ["paragraph_id"],
            ["paragraphs.id"],
            name="fk_paragraph_concepts_paragraph_id_paragraphs",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["concept_id"],
            ["concepts.id"],
            name="fk_paragraph_concepts_concept_id_concepts",
            ondelete="CASCADE",
        ),
    )
    op.create_table(
        "provision_topics",
        sa.Column("provision_id", sa.Integer(), primary_key=True),
        sa.Column("topic_id", sa.Integer(), primary_key=True),
        sa.ForeignKeyConstraint(
            ["provision_id"],
            ["provisions.id"],
            name="fk_provision_topics_provision_id_provisions",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["topic_id"],
            ["topics.id"],
            name="fk_provision_topics_topic_id_topics",
            ondelete="CASCADE",
        ),
    )
    op.create_table(
        "provision_concepts",
        sa.Column("provision_id", sa.Integer(), primary_key=True),
        sa.Column("concept_id", sa.Integer(), primary_key=True),
        sa.ForeignKeyConstraint(
            ["provision_id"],
            ["provisions.id"],
            name="fk_provision_concepts_provision_id_provisions",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["concept_id"],
            ["concepts.id"],
            name="fk_provision_concepts_concept_id_concepts",
            ondelete="CASCADE",
        ),
    )
    op.create_table(
        "aliases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("case_id", sa.Integer(), nullable=False),
        sa.Column("short_name", sa.String(), nullable=False),
        sa.Column("expanded_text", sa.String(), nullable=False),
        sa.Column("target_act_id", sa.Integer(), nullable=True),
        sa.Column("target_case_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
            name="fk_aliases_case_id_cases",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["target_act_id"],
            ["acts.id"],
            name="fk_aliases_target_act_id_acts",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["target_case_id"],
            ["cases.id"],
            name="fk_aliases_target_case_id_cases",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("case_id", "short_name", name="uq_aliases_case_id_short_name"),
    )
    op.create_index("ix_aliases_case_id", "aliases", ["case_id"])
    op.create_table(
        "references",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_paragraph_id", sa.Integer(), nullable=False),
        sa.Column("alias_id", sa.Integer(), nullable=True),
        sa.Column("target_act_id", sa.Integer(), nullable=True),
        sa.Column("target_provision_id", sa.Integer(), nullable=True),
        sa.Column("target_case_id", sa.Integer(), nullable=True),
        sa.Column("target_paragraph_id", sa.Integer(), nullable=True),
        sa.Column("quoted_text", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "(target_act_id IS NOT NULL)::int"
            " + (target_provision_id IS NOT NULL)::int"
            " + (target_case_id IS NOT NULL)::int"
            " + (target_paragraph_id IS NOT NULL)::int"
            " <= 1",
            name="at_most_one_target",
        ),
        sa.ForeignKeyConstraint(
            ["source_paragraph_id"],
            ["paragraphs.id"],
            name="fk_references_source_paragraph_id_paragraphs",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["alias_id"],
            ["aliases.id"],
            name="fk_references_alias_id_aliases",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["target_act_id"],
            ["acts.id"],
            name="fk_references_target_act_id_acts",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["target_provision_id"],
            ["provisions.id"],
            name="fk_references_target_provision_id_provisions",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["target_case_id"],
            ["cases.id"],
            name="fk_references_target_case_id_cases",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["target_paragraph_id"],
            ["paragraphs.id"],
            name="fk_references_target_paragraph_id_paragraphs",
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_references_source_paragraph_id", "references", ["source_paragraph_id"])


def downgrade() -> None:
    op.drop_table("references")
    op.drop_table("aliases")
    op.drop_table("provision_concepts")
    op.drop_table("provision_topics")
    op.drop_table("paragraph_concepts")
    op.drop_table("paragraph_topics")
    op.drop_table("paragraphs")
    op.drop_table("provisions")
    op.drop_table("case_concepts")
    op.drop_table("case_topics")
    op.drop_table("case_counsels")
    op.drop_table("case_parties")
    op.drop_table("case_judges")
    op.drop_table("cases")
    op.drop_table("subsidiary_legislations")
    op.drop_table("act_versions")
    op.drop_table("acts")
    op.drop_table("functional_roles")
    op.drop_table("concepts")
    op.drop_table("topics")
    op.drop_table("counsels")
    op.drop_table("parties")
    op.drop_table("judges")
    op.drop_table("courts")
    op.execute("DROP EXTENSION IF EXISTS vector")
