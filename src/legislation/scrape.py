from time import sleep

from sqlalchemy.orm import Session

from src.database import get_raw_source_session_maker
from src.legislation.client import StatutesOnlineClient
from src.legislation.schema import LegislationScrapeLimits
from src.logger import get_logger
from src.notify.schema import FailureItem, StageReport, StageReporter, emit_report, stored_failures
from src.raw.models.cases import FetchStatus
from src.raw.models.legislation import RawAct
from src.raw.repository import RawLegislationRepository

FETCH_PROGRESS_EVERY = 20

logger = get_logger(__name__)


class LegislationRawScraper:
    def __init__(self, limits: LegislationScrapeLimits | None = None) -> None:
        self.limits = limits or LegislationScrapeLimits()

    def run(
        self,
        max_acts: int | None = None,
        max_versions: int | None = None,
        reporter: StageReporter | None = None,
    ) -> None:
        session_maker = get_raw_source_session_maker()

        with session_maker() as session, StatutesOnlineClient(self.limits) as client:
            scraper = LegislationActScraper(
                RawLegislationRepository(session),
                client,
                self.limits,
                session,
            )

            added_acts = scraper.discover_acts(max_acts)
            session.commit()

            emit_report(
                reporter,
                StageReport(stage="discover acts", counts={"new acts": added_acts}),
            )

            added_versions = scraper.discover_versions(max_acts)
            session.commit()

            emit_report(
                reporter,
                StageReport(stage="discover versions", counts={"new versions": added_versions}),
            )

            fetched = scraper.fetch_pending(max_versions, reporter)
            session.commit()

        logger.info(
            "Act scrape finished: %s new acts, %s new versions, %s versions fetched",
            added_acts,
            added_versions,
            fetched.counts.get("fetched", 0),
        )

    def scrape_subsidiary_legislation(
        self,
        max_acts: int | None = None,
        max_versions: int | None = None,
    ) -> None:
        """Scrape SL listed under stored acts. Does not browse the global SL catalog."""
        session_maker = get_raw_source_session_maker()

        with session_maker() as session, StatutesOnlineClient(self.limits) as client:
            repository = RawLegislationRepository(session)
            scraper = LegislationSubsidiaryScraper(repository, client, self.limits, session)
            acts = repository.get_acts(max_acts)

            added_instruments = scraper.discover_instruments(acts)
            session.commit()

            added_versions = scraper.discover_versions(acts)
            session.commit()

            fetched = scraper.fetch_pending(acts, max_versions)
            session.commit()

        logger.info(
            (
                "Subsidiary legislation scrape finished: %s new instruments, "
                "%s new versions, %s versions fetched"
            ),
            added_instruments,
            added_versions,
            fetched,
        )


class LegislationActScraper:
    def __init__(
        self,
        repository: RawLegislationRepository,
        client: StatutesOnlineClient,
        limits: LegislationScrapeLimits,
        session: Session,
    ) -> None:
        self.repository = repository
        self.client = client
        self.limits = limits
        self.session = session

    def discover_acts(self, max_acts: int | None = None) -> int:
        listings = self.client.list_current_acts(max_acts)
        existing = self.repository.get_existing_act_slugs(
            [listing.slug for listing in listings]
        )
        added = 0

        for index, listing in enumerate(listings, start=1):
            if listing.slug not in existing:
                self.repository.add_discovered_act(
                    listing.slug,
                    listing.title,
                    listing.source_url,
                )
                added += 1
            if index % self.limits.discover_persist_every == 0:
                self.session.commit()

        logger.info("Discovered %s acts (%s new)", len(listings), added)
        return added

    def discover_versions(self, max_acts: int | None = None) -> int:
        added = 0
        acts = self.repository.get_acts(max_acts)

        for act in acts:
            existing = self.repository.get_existing_act_version_dates(act.id)
            for listing in self.client.list_versions(act.source_url):
                if listing.valid_from in existing:
                    continue

                self.repository.add_discovered_act_version(
                    act,
                    listing.valid_from,
                    listing.is_current,
                    listing.source_url,
                )
                added += 1

            self.session.commit()
            sleep(self.limits.browse_pause_seconds)

        logger.info("Discovered %s act versions across %s acts", added, len(acts))
        return added

    def fetch_pending(
        self,
        max_versions: int | None = None,
        reporter: StageReporter | None = None,
    ) -> StageReport:
        versions = self.repository.get_act_versions_pending_fetch(max_versions)
        logger.info("Fetching %s pending act versions", len(versions))

        fetched = 0
        failed = 0
        incomplete = 0
        last_progress = 0
        failures: list[FailureItem] = []

        for version in versions:
            result = self.client.fetch_document(version.source_url)
            self.repository.save_act_version_fetch(version, result)
            self.session.commit()
            fetched += 1

            if result.fetch_status != FetchStatus.SUCCESS:
                failed += 1
                reason = result.fetch_error or result.fetch_status.value
                failures.append(FailureItem(version.source_url, reason))
                logger.warning(
                    "Act version fetch failed for %s (%s): %s",
                    version.source_url,
                    result.fetch_status.value,
                    result.fetch_error,
                )
            elif result.expected_provision_count != result.extracted_provision_count:
                incomplete += 1
                failures.append(
                    FailureItem(
                        version.source_url,
                        (
                            f"assembled {result.extracted_provision_count} of "
                            f"{result.expected_provision_count} TOC items"
                        ),
                    )
                )
                logger.warning(
                    "Act version %s assembled %s of %s TOC items",
                    version.source_url,
                    result.extracted_provision_count,
                    result.expected_provision_count,
                )

            if fetched - last_progress >= FETCH_PROGRESS_EVERY:
                last_progress = fetched

                emit_report(
                    reporter,
                    StageReport(
                        stage="fetch",
                        counts={"fetched": fetched, "failed": failed, "incomplete": incomplete},
                        failures=stored_failures(failures),
                        in_progress=True,
                        done=fetched,
                        total=len(versions),
                    ),
                )

            sleep(self.limits.document_pause_seconds)

        logger.info("Fetched %s act versions, %s failed", fetched, failed)
        report = StageReport(
            stage="fetch",
            counts={"fetched": fetched, "failed": failed, "incomplete": incomplete},
            failures=stored_failures(failures),
        )

        emit_report(reporter, report)

        return report


class LegislationSubsidiaryScraper:
    def __init__(
        self,
        repository: RawLegislationRepository,
        client: StatutesOnlineClient,
        limits: LegislationScrapeLimits,
        session: Session,
    ) -> None:
        self.repository = repository
        self.client = client
        self.limits = limits
        self.session = session

    def discover_instruments(self, acts: list[RawAct]) -> int:
        added = 0

        for act in acts:
            listings = self.client.list_subsidiary_legislations(act.source_url)
            existing = self.repository.get_existing_subsidiary_slugs(
                [listing.slug for listing in listings]
            )

            for listing in listings:
                if listing.slug in existing:
                    continue

                self.repository.add_discovered_subsidiary_legislation(
                    act.id,
                    listing.slug,
                    listing.title,
                    listing.number,
                    listing.source_url,
                )
                added += 1

            self.session.commit()
            sleep(self.limits.browse_pause_seconds)

        logger.info("Discovered %s subsidiary legislations from %s stored acts", added, len(acts))
        return added

    def discover_versions(self, acts: list[RawAct]) -> int:
        added = 0
        instruments = self.repository.get_subsidiary_legislations_for_acts(
            [act.id for act in acts]
        )

        for instrument in instruments:
            existing = self.repository.get_existing_subsidiary_version_dates(instrument.id)
            for listing in self.client.list_versions(instrument.source_url):
                if listing.valid_from in existing:
                    continue

                self.repository.add_discovered_subsidiary_legislation_version(
                    instrument,
                    listing.valid_from,
                    listing.is_current,
                    listing.source_url,
                )
                added += 1

            self.session.commit()
            sleep(self.limits.browse_pause_seconds)

        logger.info(
            "Discovered %s subsidiary legislation versions across %s instruments",
            added,
            len(instruments),
        )
        return added

    def fetch_pending(self, acts: list[RawAct], max_versions: int | None) -> int:
        instruments = self.repository.get_subsidiary_legislations_for_acts(
            [act.id for act in acts]
        )
        versions = self.repository.get_subsidiary_versions_pending_fetch(
            [instrument.id for instrument in instruments],
            max_versions,
        )
        logger.info("Fetching %s pending subsidiary legislation versions", len(versions))

        fetched = 0
        failed = 0

        for version in versions:
            result = self.client.fetch_document(version.source_url)
            self.repository.save_subsidiary_version_fetch(version, result)
            self.session.commit()
            fetched += 1

            if result.fetch_status != FetchStatus.SUCCESS:
                failed += 1
                logger.warning(
                    "Subsidiary legislation version fetch failed for %s (%s): %s",
                    version.source_url,
                    result.fetch_status.value,
                    result.fetch_error,
                )
            elif result.expected_provision_count != result.extracted_provision_count:
                logger.warning(
                    "Subsidiary legislation version %s assembled %s of %s TOC items",
                    version.source_url,
                    result.extracted_provision_count,
                    result.expected_provision_count,
                )

            sleep(self.limits.document_pause_seconds)

        logger.info(
            "Fetched %s subsidiary legislation versions, %s failed",
            fetched,
            failed,
        )
        return fetched
