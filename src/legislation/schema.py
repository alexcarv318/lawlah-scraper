from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from src.raw.models.cases import FetchStatus


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
