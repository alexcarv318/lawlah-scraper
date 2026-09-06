from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from src.raw.models.cases import CaseDocumentLayout, FetchStatus, ParseStatus


@dataclass(frozen=True)
class ScrapeLimits:
    search_page_length: int = 20
    search_pause_seconds: float = 0.8
    document_batch_size: int = 8
    document_workers: int = 4
    document_pause_seconds: float = 1.0
    request_timeout_seconds: float = 30.0
    retry_limit: int = 3
    gateway_retry_limit: int = 6
    retry_backoff_seconds: float = 2.0
    discover_persist_every: int = 25
    fetch_persist_every: int = 100
    parse_persist_every: int = 100
    promote_persist_every: int = 25
    resolve_persist_every: int = 200


@dataclass(frozen=True)
class SearchHit:
    citation: str
    decision_date: date | None
    payload: dict[str, object]


@dataclass(frozen=True)
class DocumentFetch:
    citation: str
    source_url: str
    http_status: int | None
    fetch_status: FetchStatus
    fetch_error: str | None
    html: str | None
    source_metadata: dict[str, object] | None
    decision_date: date | None


@dataclass(frozen=True)
class DocumentParse:
    layout: CaseDocumentLayout
    parse_status: ParseStatus
    expected_paragraph_count: int | None
    extracted_paragraph_count: int | None
    needs_review: bool

    @staticmethod
    def failed() -> DocumentParse:
        return DocumentParse(
            layout=CaseDocumentLayout.UNKNOWN,
            parse_status=ParseStatus.FAILED,
            expected_paragraph_count=None,
            extracted_paragraph_count=None,
            needs_review=True,
        )

    @staticmethod
    def unknown_layout() -> DocumentParse:
        return DocumentParse(
            layout=CaseDocumentLayout.UNKNOWN,
            parse_status=ParseStatus.UNKNOWN_LAYOUT,
            expected_paragraph_count=None,
            extracted_paragraph_count=None,
            needs_review=True,
        )

    @staticmethod
    def from_counts(
        layout: CaseDocumentLayout,
        expected_paragraph_count: int | None,
        extracted_paragraph_count: int,
        complete: bool,
    ) -> DocumentParse:
        return DocumentParse(
            layout=layout,
            parse_status=ParseStatus.COMPLETE if complete else ParseStatus.INCOMPLETE,
            expected_paragraph_count=expected_paragraph_count,
            extracted_paragraph_count=extracted_paragraph_count,
            needs_review=not complete,
        )


@dataclass(frozen=True)
class ExtractedParagraph:
    ordinal: int
    content: str
