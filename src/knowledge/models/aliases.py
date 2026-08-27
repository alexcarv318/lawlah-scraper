from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.database import KnowledgeBase


class Alias(KnowledgeBase):
    __tablename__ = "aliases"

    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    short_name: Mapped[str] = mapped_column(String)
    expanded_text: Mapped[str] = mapped_column(String)
    target_act_id: Mapped[int | None] = mapped_column(ForeignKey("acts.id", ondelete="SET NULL"))
    target_case_id: Mapped[int | None] = mapped_column(ForeignKey("cases.id", ondelete="SET NULL"))

    __table_args__ = (
        UniqueConstraint("case_id", "short_name", name="uq_aliases_case_id_short_name"),
    )
