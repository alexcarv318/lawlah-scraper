from datetime import date

from sqlalchemy import Date, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from src.database import KnowledgeBase


class Case(KnowledgeBase):
    __tablename__ = "cases"

    id: Mapped[int] = mapped_column(primary_key=True)
    court_id: Mapped[int] = mapped_column(ForeignKey("courts.id", ondelete="RESTRICT"), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    uri: Mapped[str] = mapped_column(String, unique=True)
    title: Mapped[str] = mapped_column(String)
    neutral_citation: Mapped[str] = mapped_column(String, unique=True)
    case_number: Mapped[str | None] = mapped_column(String)


class Court(KnowledgeBase):
    __tablename__ = "courts"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String, unique=True)
    name: Mapped[str] = mapped_column(String)


class Judge(KnowledgeBase):
    __tablename__ = "judges"

    id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str] = mapped_column(String)
    title: Mapped[str | None] = mapped_column(String)


class CaseJudge(KnowledgeBase):
    __tablename__ = "case_judges"

    case_id: Mapped[int] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"),
        primary_key=True,
    )
    judge_id: Mapped[int] = mapped_column(
        ForeignKey("judges.id", ondelete="CASCADE"),
        primary_key=True,
    )


class Party(KnowledgeBase):
    __tablename__ = "parties"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True)


class CaseParty(KnowledgeBase):
    __tablename__ = "case_parties"

    case_id: Mapped[int] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"),
        primary_key=True,
    )
    party_id: Mapped[int] = mapped_column(
        ForeignKey("parties.id", ondelete="CASCADE"),
        primary_key=True,
    )
    role: Mapped[str | None] = mapped_column(String)


class Counsel(KnowledgeBase):
    __tablename__ = "counsels"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True)


class CaseCounsel(KnowledgeBase):
    __tablename__ = "case_counsels"

    case_id: Mapped[int] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"),
        primary_key=True,
    )
    counsel_id: Mapped[int] = mapped_column(
        ForeignKey("counsels.id", ondelete="CASCADE"),
        primary_key=True,
    )
    represents: Mapped[str | None] = mapped_column(String)
