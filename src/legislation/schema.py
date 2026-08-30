from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from src.knowledge.models.provisions import ProvisionKind
from src.raw.models.cases import FetchStatus, ParseStatus


@dataclass(frozen=True)
class LegislationScrapeLimits:
    browse_page_size: int = 500
    browse_pause_seconds: float = 0.5
    document_pause_seconds: float = 1.0
    request_timeout_seconds: float = 60.0
    page_load_timeout_seconds: float = 45.0
    retry_limit: int = 3
    retry_backoff_seconds: float = 2.0
    content_stall_seconds: float = 8.0
    http_467_pause_seconds: float = 300.0
    http_467_pause_cap_seconds: float = 2400.0


@dataclass(frozen=True)
class ActListing:
    slug: str
    title: str
    source_url: str


@dataclass(frozen=True)
class SubsidiaryListing:
    slug: str
    title: str
    number: str
    source_url: str


@dataclass(frozen=True)
class VersionListing:
    valid_from: date
    is_current: bool
    source_url: str


@dataclass(frozen=True)
class LegislationFetch:
    source_url: str
    http_status: int | None
    fetch_status: FetchStatus
    fetch_error: str | None
    html: str | None
    source_metadata: dict[str, object] | None
    expected_provision_count: int | None
    extracted_provision_count: int | None


@dataclass
class ExtractedProvision:
    kind: ProvisionKind
    citation: str | None
    heading: str | None
    content: str | None
    amendment_note: str | None
    anchor: str | None
    children: list[ExtractedProvision]


@dataclass(frozen=True)
class FlattenedProvision:
    kind: ProvisionKind
    citation: str | None
    heading: str | None
    content: str | None
    amendment_note: str | None
    anchor: str | None
    parent_index: int | None
    ordinal: int
    level: int
    descendant_count: int


@dataclass(frozen=True)
class ExtractedDefinition:
    term: str
    definition: str


@dataclass(frozen=True)
class LegislationParse:
    parse_status: ParseStatus
    expected_provision_count: int | None
    extracted_provision_count: int | None
    needs_review: bool
    provisions: list[ExtractedProvision]
    definitions: list[ExtractedDefinition]

    @staticmethod
    def failed() -> LegislationParse:
        return LegislationParse(
            parse_status=ParseStatus.FAILED,
            expected_provision_count=None,
            extracted_provision_count=None,
            needs_review=True,
            provisions=[],
            definitions=[],
        )

    @staticmethod
    def unknown_layout() -> LegislationParse:
        return LegislationParse(
            parse_status=ParseStatus.UNKNOWN_LAYOUT,
            expected_provision_count=None,
            extracted_provision_count=None,
            needs_review=True,
            provisions=[],
            definitions=[],
        )

    @staticmethod
    def from_tree(
        expected_provision_count: int,
        extracted_provision_count: int,
        provisions: list[ExtractedProvision],
        definitions: list[ExtractedDefinition],
    ) -> LegislationParse:
        complete = (
            expected_provision_count > 0
            and extracted_provision_count == expected_provision_count
        )
        return LegislationParse(
            parse_status=ParseStatus.COMPLETE if complete else ParseStatus.INCOMPLETE,
            expected_provision_count=expected_provision_count,
            extracted_provision_count=extracted_provision_count,
            needs_review=not complete,
            provisions=provisions,
            definitions=definitions,
        )
