from datetime import date
from time import sleep
from types import TracebackType
from typing import Self
from urllib.parse import quote

import httpx

from src.cases.parse import CaseDocumentParser
from src.cases.schema import DocumentFetch, ScrapeLimits, SearchHit
from src.logger import get_logger
from src.raw.models.cases import FetchStatus

RETRYABLE_STATUS = frozenset({429, 502, 503, 504})
GATEWAY_STATUS = frozenset({502, 503, 504})
RETRY_WAIT_CAP_SECONDS = 60.0

SEARCH_URL = "https://api.lawnet.com/search-service/api/lawnetcore/search/supreme-court"
DOCUMENT_URL = "https://api.lawnet.com/search-service/api/lawnetcore/document/citation/v1"
DOCUMENT_PAGE_URL = "https://www.lawnet.com/openlaw/cases/citation/{citation}?ref=sg-sc"

LAWNET_HEADERS = {
    "accept": "application/json",
    "content-type": "application/json",
    "auth-type": "Public",
    "origin": "https://www.lawnet.com",
    "user-agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
}

LAWNET_REQUEST_INFO = {
    "userId": 0,
    "userName": None,
    "sessionId": None,
    "appId": "APP_LAWNET_ONE",
    "userDevice": {
        "ua": LAWNET_HEADERS["user-agent"],
        "isBot": False,
    },
}

logger = get_logger(__name__)


class LawNetResponseError(Exception):
    """LawNet returned a payload we cannot read."""


class LawNetClient:
    def __init__(self, limits: ScrapeLimits) -> None:
        self.limits = limits
        self.http = httpx.Client(headers=LAWNET_HEADERS, timeout=limits.request_timeout_seconds)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self.http.close()

    def search_page(self, start: int) -> list[SearchHit]:
        body = self.post_json(
            SEARCH_URL,
            {
                "info": LAWNET_REQUEST_INFO,
                "data": {
                    "searchDatabase": "allOfLawnet",
                    "searchQuery": "",
                    "start": start,
                    "pageLength": self.limits.search_page_length,
                    "searchSource": None,
                    "filters": None,
                    "orderBy": "date-desc",
                    "includeDraft": "false",
                },
            },
        )
        return LawNetClient.parse_search_hits(body)

    def fetch_document(self, citation: str) -> DocumentFetch:
        source_url = DOCUMENT_PAGE_URL.format(citation=quote(citation))

        try:
            body = self.post_json(
                DOCUMENT_URL,
                {"info": LAWNET_REQUEST_INFO, "data": {"citation": citation, "q": None}},
            )
        except httpx.HTTPError as error:
            http_status = error.response.status_code if isinstance(error, httpx.HTTPStatusError) else None
            fetch_status = (
                LawNetClient.classify_http_status(http_status)
                if http_status is not None
                else FetchStatus.ERROR
            )
            return DocumentFetch(
                citation=citation,
                source_url=source_url,
                http_status=http_status,
                fetch_status=fetch_status,
                fetch_error=str(error),
                html=None,
                source_metadata=None,
                decision_date=None,
            )

        return LawNetClient.document_from_body(citation, source_url, body)

    def post_json(self, url: str, payload: dict[str, object]) -> dict[str, object]:
        attempt = 0
        while True:
            response = self.http.post(url, json=payload)
            if self.should_retry(response.status_code, attempt):
                wait_seconds = min(
                    RETRY_WAIT_CAP_SECONDS,
                    self.limits.retry_backoff_seconds * (2**attempt),
                )
                logger.warning(
                    "LawNet %s for %s, retrying in %s seconds (attempt %s)",
                    response.status_code,
                    url,
                    wait_seconds,
                    attempt + 1,
                )
                sleep(wait_seconds)
                attempt += 1
                continue

            response.raise_for_status()
            body = response.json()
            if not isinstance(body, dict):
                raise LawNetResponseError("LawNet response is not an object")
            return body

    def should_retry(self, status_code: int, attempt: int) -> bool:
        if status_code not in RETRYABLE_STATUS:
            return False
        limit = (
            self.limits.gateway_retry_limit
            if status_code in GATEWAY_STATUS
            else self.limits.retry_limit
        )
        return attempt < limit

    @staticmethod
    def parse_search_hits(body: dict[str, object]) -> list[SearchHit]:
        data = body.get("data")
        if not isinstance(data, dict):
            raise LawNetResponseError("Search response has no data object")

        results = data.get("results")
        if results is None:
            return []
        if not isinstance(results, list):
            raise LawNetResponseError("Search results are not a list")

        hits: list[SearchHit] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            citation = item.get("ncitation")
            if not isinstance(citation, str) or not citation.strip():
                continue
            hits.append(
                SearchHit(
                    citation=citation.strip(),
                    decision_date=LawNetClient.parse_decision_date(item.get("dates")),
                    payload=LawNetClient.string_keyed(item) or {},
                )
            )
        return hits

    @staticmethod
    def parse_decision_date(dates: object) -> date | None:
        if not isinstance(dates, list) or not dates:
            return None
        return LawNetClient.parse_iso_date(dates[0])

    @staticmethod
    def parse_iso_date(value: object) -> date | None:
        if not isinstance(value, str) or len(value) < 10:
            return None
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None

    @staticmethod
    def document_from_body(
        citation: str,
        source_url: str,
        body: dict[str, object],
    ) -> DocumentFetch:
        data = body.get("data")
        if not isinstance(data, dict):
            return DocumentFetch(
                citation=citation,
                source_url=source_url,
                http_status=200,
                fetch_status=FetchStatus.NOT_FOUND,
                fetch_error="Document payload has no data object",
                html=None,
                source_metadata=None,
                decision_date=None,
            )

        source_metadata = LawNetClient.string_keyed(data.get("metadata"))
        decision_date = (
            LawNetClient.parse_iso_date(source_metadata.get("DecisionDate"))
            if source_metadata is not None
            else None
        )
        html = LawNetClient.document_html(data)
        if html is None:
            return DocumentFetch(
                citation=citation,
                source_url=source_url,
                http_status=200,
                fetch_status=FetchStatus.NOT_FOUND,
                fetch_error="Document HTML is missing",
                html=None,
                source_metadata=source_metadata,
                decision_date=decision_date,
            )
        return DocumentFetch(
            citation=citation,
            source_url=source_url,
            http_status=200,
            fetch_status=FetchStatus.SUCCESS,
            fetch_error=None,
            html=html,
            source_metadata=source_metadata,
            decision_date=decision_date,
        )

    @staticmethod
    def document_html(data: dict[str, object]) -> str | None:
        candidates: list[tuple[int, str]] = []

        single = data.get("document")
        if isinstance(single, str) and single.strip():
            candidates.append((1, single))

        documents = data.get("documents")
        if isinstance(documents, dict):
            for path, value in documents.items():
                if not isinstance(value, str) or not value.strip():
                    continue
                normalized = str(path).replace("\\", "/").lower()
                if "/judgment/" in normalized or normalized.startswith("judgment/"):
                    rank = 0
                elif "/slr/" in normalized or normalized.startswith("slr/"):
                    rank = 2
                else:
                    rank = 3
                candidates.append((rank, value))

        if len(candidates) == 1:
            return candidates[0][1]
        return CaseDocumentParser.preferred_html(candidates)

    @staticmethod
    def string_keyed(value: object) -> dict[str, object] | None:
        if not isinstance(value, dict):
            return None
        return {str(key): item for key, item in value.items()}

    @staticmethod
    def classify_http_status(status_code: int) -> FetchStatus:
        if status_code == 404:
            return FetchStatus.NOT_FOUND
        return FetchStatus.ERROR
