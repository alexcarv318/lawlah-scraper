from datetime import date

from sqlalchemy import Boolean, Date, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from src.database import KnowledgeBase


class Act(KnowledgeBase):
    __tablename__ = "acts"

    id: Mapped[int] = mapped_column(primary_key=True)
    uri: Mapped[str] = mapped_column(String, unique=True)
    title: Mapped[str] = mapped_column(String)


class ActVersion(KnowledgeBase):
    __tablename__ = "act_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    act_id: Mapped[int] = mapped_column(ForeignKey("acts.id", ondelete="CASCADE"), index=True)
    uri: Mapped[str] = mapped_column(String, unique=True)
    valid_from: Mapped[date] = mapped_column(Date)
    is_current: Mapped[bool] = mapped_column(Boolean)

    __table_args__ = (
        UniqueConstraint("act_id", "valid_from", name="uq_act_versions_act_id_valid_from"),
        Index(
            "uq_act_versions_current",
            "act_id",
            unique=True,
            postgresql_where=text("is_current"),
        ),
    )


class SubsidiaryLegislation(KnowledgeBase):
    __tablename__ = "subsidiary_legislations"

    id: Mapped[int] = mapped_column(primary_key=True)
    act_id: Mapped[int | None] = mapped_column(ForeignKey("acts.id", ondelete="SET NULL"), index=True)
    uri: Mapped[str] = mapped_column(String, unique=True)
    title: Mapped[str] = mapped_column(String)
    number: Mapped[str] = mapped_column(String)
    date: Mapped[date] = mapped_column(Date)
