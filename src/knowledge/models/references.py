from enum import StrEnum

from sqlalchemy import ARRAY, CheckConstraint, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.database import KnowledgeBase


class CitationKind(StrEnum):
    CASE = "case"
    ACT = "act"
    PROVISION = "provision"
    RULE = "rule"
    SELF = "self"
    BOOK = "book"


class Reference(KnowledgeBase):
    __tablename__ = "references"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_paragraph_id: Mapped[int] = mapped_column(
        ForeignKey("paragraphs.id", ondelete="CASCADE"),
        index=True,
    )
    alias_id: Mapped[int | None] = mapped_column(ForeignKey("aliases.id", ondelete="SET NULL"))
    target_act_id: Mapped[int | None] = mapped_column(ForeignKey("acts.id", ondelete="SET NULL"))
    target_provision_id: Mapped[int | None] = mapped_column(
        ForeignKey("provisions.id", ondelete="SET NULL")
    )
    target_case_id: Mapped[int | None] = mapped_column(ForeignKey("cases.id", ondelete="SET NULL"))
    target_paragraph_id: Mapped[int | None] = mapped_column(
        ForeignKey("paragraphs.id", ondelete="SET NULL")
    )
    quoted_text: Mapped[str] = mapped_column(Text)
    kind: Mapped[CitationKind | None] = mapped_column(
        Enum(
            CitationKind,
            native_enum=False,
            length=32,
            values_callable=lambda enum_class: [member.value for member in enum_class],
        )
    )
    alias_short_name: Mapped[str | None] = mapped_column(String)
    title: Mapped[str | None] = mapped_column(String)
    neutral_citation: Mapped[str | None] = mapped_column(String)
    slr_citation: Mapped[str | None] = mapped_column(String)
    edition: Mapped[str | None] = mapped_column(String)
    provision_citations: Mapped[list[str]] = mapped_column(
        ARRAY(String),
        default=list,
        server_default="{}",
    )
    paragraph_pins: Mapped[list[int]] = mapped_column(
        ARRAY(Integer),
        default=list,
        server_default="{}",
    )

    __table_args__ = (
        CheckConstraint(
            "(target_act_id IS NOT NULL)::int"
            " + (target_provision_id IS NOT NULL)::int"
            " + (target_case_id IS NOT NULL)::int"
            " + (target_paragraph_id IS NOT NULL)::int"
            " <= 1",
            name="at_most_one_target",
        ),
    )
