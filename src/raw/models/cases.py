from datetime import date
from enum import StrEnum

from sqlalchemy import Date, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.database import RawSourceBase


class RawCaseStatus(StrEnum):
    DISCOVERED = "discovered"
    FETCH_FAILED = "fetch_failed"
    PARSE_INCOMPLETE = "parse_incomplete"
    COMPLETE = "complete"
    NEEDS_REVIEW = "needs_review"


class FetchStatus(StrEnum):
    NOT_FETCHED = "not_fetched"
    SUCCESS = "success"
    NOT_FOUND = "not_found"
    ERROR = "error"


class ParseStatus(StrEnum):
    NOT_PARSED = "not_parsed"
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    UNKNOWN_LAYOUT = "unknown_layout"
    FAILED = "failed"


class CaseDocumentLayout(StrEnum):
    MODERN_JUDG1 = "modern_judg1"
    NUMBERED_PLAIN_P = "numbered_plain_p"
    UNNUMBERED_BR = "unnumbered_br"
    SINGLE_BLOCK = "single_block"
    UNKNOWN = "unknown"


class RawCase(RawSourceBase):
    __tablename__ = "raw_cases"

    id: Mapped[int] = mapped_column(primary_key=True)
    neutral_citation: Mapped[str] = mapped_column(String, unique=True)
    date: Mapped[date | None] = mapped_column(Date, index=True)
    status: Mapped[RawCaseStatus] = mapped_column(
        Enum(
            RawCaseStatus,
            native_enum=False,
            length=32,
            values_callable=lambda enum_class: [member.value for member in enum_class],
        ),
        index=True,
    )
    search_result: Mapped[dict[str, object] | None] = mapped_column(JSONB)


class RawCaseDocument(RawSourceBase):
    __tablename__ = "raw_case_documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    raw_case_id: Mapped[int] = mapped_column(
        ForeignKey("raw_cases.id", ondelete="CASCADE"),
        unique=True,
    )
    source_url: Mapped[str] = mapped_column(Text)
    http_status: Mapped[int | None] = mapped_column(Integer)
    fetch_status: Mapped[FetchStatus] = mapped_column(
        Enum(
            FetchStatus,
            native_enum=False,
            length=32,
            values_callable=lambda enum_class: [member.value for member in enum_class],
        )
    )
    fetch_error: Mapped[str | None] = mapped_column(Text)
    html: Mapped[str | None] = mapped_column(Text)
    source_metadata: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    layout: Mapped[CaseDocumentLayout | None] = mapped_column(
        Enum(
            CaseDocumentLayout,
            native_enum=False,
            length=32,
            values_callable=lambda enum_class: [member.value for member in enum_class],
        )
    )
    parse_status: Mapped[ParseStatus] = mapped_column(
        Enum(
            ParseStatus,
            native_enum=False,
            length=32,
            values_callable=lambda enum_class: [member.value for member in enum_class],
        )
    )
    expected_paragraph_count: Mapped[int | None] = mapped_column(Integer)
    extracted_paragraph_count: Mapped[int | None] = mapped_column(Integer)
    needs_review: Mapped[bool]
    promoted: Mapped[bool]
