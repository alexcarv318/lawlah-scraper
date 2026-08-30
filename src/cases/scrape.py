from concurrent.futures import ThreadPoolExecutor
from time import sleep

from src.cases.client import LawNetClient
from src.cases.schema import DocumentFetch, ScrapeLimits
from src.database import get_raw_source_session_maker
from src.logger import get_logger
from src.raw.models.cases import FetchStatus
from src.raw.repository import RawCaseRepository

logger = get_logger(__name__)


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

            added = CaseSearchScraper(repository, client).discover(max_search_pages, until_latest_stored_date)
            session.commit()

            fetched = CaseDocumentScraper(repository, self.limits).fetch_pending(max_documents)
            session.commit()

        logger.info("Raw scrape finished: %s new cases, %s documents fetched", added, fetched)
