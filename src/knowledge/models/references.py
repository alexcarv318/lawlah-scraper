from sqlalchemy import CheckConstraint, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.database import KnowledgeBase


class Reference(KnowledgeBase):
    __tablename__ = "references"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_paragraph_id: Mapped[int] = mapped_column(ForeignKey("paragraphs.id", ondelete="CASCADE"), index=True)
    alias_id: Mapped[int | None] = mapped_column(ForeignKey("aliases.id", ondelete="SET NULL"))
    target_act_id: Mapped[int | None] = mapped_column(ForeignKey("acts.id", ondelete="SET NULL"))
    target_provision_id: Mapped[int | None] = mapped_column(ForeignKey("provisions.id", ondelete="SET NULL"))
    target_case_id: Mapped[int | None] = mapped_column(ForeignKey("cases.id", ondelete="SET NULL"))
    target_paragraph_id: Mapped[int | None] = mapped_column(ForeignKey("paragraphs.id", ondelete="SET NULL"))
    quoted_text: Mapped[str] = mapped_column(Text)

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