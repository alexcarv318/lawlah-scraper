from enum import StrEnum

from sqlalchemy import Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.database import KnowledgeBase


class FunctionalRoleAppliesTo(StrEnum):
    CASE = "case"
    LEGISLATION = "legislation"


class Topic(KnowledgeBase):
    __tablename__ = "topics"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True)
    description: Mapped[str] = mapped_column(Text)


class Concept(KnowledgeBase):
    __tablename__ = "concepts"

    id: Mapped[int] = mapped_column(primary_key=True)
    topic_id: Mapped[int] = mapped_column(ForeignKey("topics.id", ondelete="RESTRICT"), index=True)
    name: Mapped[str] = mapped_column(String, unique=True)
    description: Mapped[str] = mapped_column(Text)


class FunctionalRole(KnowledgeBase):
    __tablename__ = "functional_roles"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True)
    description: Mapped[str] = mapped_column(Text)
    applies_to: Mapped[FunctionalRoleAppliesTo] = mapped_column(
        Enum(
            FunctionalRoleAppliesTo,
            native_enum=False,
            length=32,
            values_callable=lambda enum_class: [member.value for member in enum_class],
        )
    )


class CaseTopic(KnowledgeBase):
    __tablename__ = "case_topics"

    case_id: Mapped[int] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"),
        primary_key=True,
    )
    topic_id: Mapped[int] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"),
        primary_key=True,
    )


class CaseConcept(KnowledgeBase):
    __tablename__ = "case_concepts"

    case_id: Mapped[int] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"),
        primary_key=True,
    )
    concept_id: Mapped[int] = mapped_column(
        ForeignKey("concepts.id", ondelete="CASCADE"),
        primary_key=True,
    )


class ParagraphTopic(KnowledgeBase):
    __tablename__ = "paragraph_topics"

    paragraph_id: Mapped[int] = mapped_column(
        ForeignKey("paragraphs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    topic_id: Mapped[int] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"),
        primary_key=True,
    )


class ParagraphConcept(KnowledgeBase):
    __tablename__ = "paragraph_concepts"

    paragraph_id: Mapped[int] = mapped_column(
        ForeignKey("paragraphs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    concept_id: Mapped[int] = mapped_column(
        ForeignKey("concepts.id", ondelete="CASCADE"),
        primary_key=True,
    )


class ProvisionTopic(KnowledgeBase):
    __tablename__ = "provision_topics"

    provision_id: Mapped[int] = mapped_column(
        ForeignKey("provisions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    topic_id: Mapped[int] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"),
        primary_key=True,
    )


class ProvisionConcept(KnowledgeBase):
    __tablename__ = "provision_concepts"

    provision_id: Mapped[int] = mapped_column(
        ForeignKey("provisions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    concept_id: Mapped[int] = mapped_column(
        ForeignKey("concepts.id", ondelete="CASCADE"),
        primary_key=True,
    )
