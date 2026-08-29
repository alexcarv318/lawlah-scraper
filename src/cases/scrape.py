from concurrent.futures import ThreadPoolExecutor
from datetime import date
from time import sleep
from types import TracebackType
from typing import Self
from urllib.parse import quote

import httpx

from src.cases.parse import CaseDocumentParser
from src.cases.schema import DocumentFetch, ScrapeLimits, SearchHit
from src.database import get_raw_source_session_maker
from src.logger import get_logger
from src.raw.models.cases import FetchStatus
from src.raw.repository import RawCaseRepository

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
            if response.status_code == 429 and attempt < self.limits.retry_limit:
                wait_seconds = self.limits.retry_backoff_seconds * (2**attempt)
                logger.warning("LawNet rate limited, retrying in %s seconds", wait_seconds)
                sleep(wait_seconds)
                attempt += 1
                continue

            response.raise_for_status()
            body = response.json()
            if not isinstance(body, dict):
                raise LawNetResponseError("LawNet response is not an object")
            return body

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


class CaseSearchScraper:
    def __init__(self, repository: RawCaseRepository, client: LawNetClient) -> None:
        self.repository = repository
        self.client = client

    def discover(
        self,
        max_pages: int | None = None,
        until_latest_stored_date: bool = False,
    ) -> int:
        latest_date = self.repository.get_latest_decision_date() if until_latest_stored_date else None
        logger.info(
            "Discovering cases (max_pages=%s, until_latest_stored_date=%s, latest_stored_date=%s)",
            max_pages,
            until_latest_stored_date,
            latest_date,
        )

        added = 0
        start = 1
        page_number = 0

        while max_pages is None or page_number < max_pages:
            hits = self.client.search_page(start)
            if not hits:
                logger.info("Search returned no more results after %s pages", page_number)
                return added

            page_number += 1
            already_stored = self.repository.get_existing_citations([hit.citation for hit in hits])
            added_on_page = 0
            reached_older_cases = False

            for hit in hits:
                if (
                    latest_date is not None
                    and hit.decision_date is not None
                    and hit.decision_date < latest_date
                ):
                    reached_older_cases = True
                    continue
                if hit.citation in already_stored:
                    continue
                self.repository.add_discovered_case(hit.citation, hit.decision_date, hit.payload)
                added_on_page += 1
                added += 1

            logger.info("Search page %s: %s hits, %s new cases", page_number, len(hits), added_on_page)
            if reached_older_cases:
                logger.info(
                    "Reached cases older than %s, stopping discovery (%s new cases)",
                    latest_date,
                    added,
                )
                return added

            start += self.client.limits.search_page_length
            sleep(self.client.limits.search_pause_seconds)

        logger.info("Stopped discovery at max_pages=%s (%s new cases)", max_pages, added)
        return added


class CaseDocumentScraper:
    def __init__(self, repository: RawCaseRepository, limits: ScrapeLimits) -> None:
        self.repository = repository
        self.limits = limits

    def fetch_pending(self, max_documents: int | None = None) -> int:
        citations = self.repository.get_citations_pending_document(max_documents)
        logger.info("Fetching %s pending documents", len(citations))

        fetched = 0
        failed = 0

        for batch_start in range(0, len(citations), self.limits.document_batch_size):
            batch = citations[batch_start : batch_start + self.limits.document_batch_size]
            with ThreadPoolExecutor(max_workers=self.limits.document_workers) as pool:
                results = list(pool.map(self.fetch_one_in_worker, batch))

            for result in results:
                self.repository.save_document(result)
                fetched += 1
                if result.fetch_status != FetchStatus.SUCCESS:
                    failed += 1
                    logger.warning(
                        "Document fetch failed for %s (%s): %s",
                        result.citation,
                        result.fetch_status.value,
                        result.fetch_error,
                    )

            sleep(self.limits.document_pause_seconds)

        logger.info("Fetched %s documents, %s failed", fetched, failed)
        return fetched

    def fetch_one_in_worker(self, citation: str) -> DocumentFetch:
        with LawNetClient(self.limits) as client:
            return client.fetch_document(citation)


class CaseRawScraper:
    def __init__(self, limits: ScrapeLimits | None = None) -> None:
        self.limits = limits or ScrapeLimits()

    def run(
        self,
        max_search_pages: int | None = None,
        max_documents: int | None = None,
        until_latest_stored_date: bool = True,
    ) -> None:
        session_maker = get_raw_source_session_maker()
        with session_maker() as session, LawNetClient(self.limits) as client:
            repository = RawCaseRepository(session)
            added = CaseSearchScraper(repository, client).discover(
                max_search_pages,
                until_latest_stored_date,
            )
            session.commit()
            fetched = CaseDocumentScraper(repository, self.limits).fetch_pending(max_documents)
            session.commit()

        logger.info("Raw scrape finished: %s new cases, %s documents fetched", added, fetched)
