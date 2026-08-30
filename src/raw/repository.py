from collections.abc import Sequence
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.cases.schema import DocumentFetch, DocumentParse
from src.legislation.schema import LegislationFetch, LegislationParse
from src.raw.models.cases import (
    FetchStatus,
    ParseStatus,
    RawCase,
    RawCaseDocument,
    RawCaseStatus,
)
from src.raw.models.legislation import (
    RawAct,
    RawActVersion,
    RawLegislationStatus,
    RawSubsidiaryLegislation,
    RawSubsidiaryLegislationVersion,
)


class RawCaseRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_existing_citations(self, citations: list[str]) -> set[str]:
        if not citations:
            return set()
        stmt = select(RawCase.neutral_citation).where(RawCase.neutral_citation.in_(citations))
        found = self.session.scalars(stmt)
        return set(found)

    def get_latest_decision_date(self) -> date | None:
        return self.session.scalar(select(func.max(RawCase.date)))

    def add_discovered_case(
        self,
        citation: str,
        decision_date: date | None,
        search_result: dict[str, object],
    ) -> None:
        self.session.add(
            RawCase(
                neutral_citation=citation,
                date=decision_date,
                status=RawCaseStatus.DISCOVERED,
                search_result=search_result,
            )
        )

    @staticmethod
    def mark_promoted(document: RawCaseDocument) -> None:
        document.promoted = True

    def get_citations_pending_document(self, limit: int | None) -> list[str]:
        query = (
            select(RawCase.neutral_citation)
            .outerjoin(RawCaseDocument)
            .where(
                (RawCaseDocument.id.is_(None))
                | (RawCaseDocument.fetch_status != FetchStatus.SUCCESS)
            )
            .order_by(RawCase.date.desc().nulls_last())
        )
        if limit is not None:
            query = query.limit(limit)
        return list(self.session.scalars(query))

    def save_document(self, result: DocumentFetch) -> None:
        raw_case = self.session.scalar(
            select(RawCase).where(RawCase.neutral_citation == result.citation)
        )
        if raw_case is None:
            raise ValueError(f"No raw case for citation {result.citation}")

        document = self.session.scalar(
            select(RawCaseDocument).where(RawCaseDocument.raw_case_id == raw_case.id)
        )
        if document is None:
            document = RawCaseDocument(raw_case_id=raw_case.id)
            self.session.add(document)

        document.source_url = result.source_url
        document.http_status = result.http_status
        document.fetch_status = result.fetch_status
        document.fetch_error = result.fetch_error
        document.html = result.html
        document.source_metadata = result.source_metadata
        document.layout = None
        document.parse_status = ParseStatus.NOT_PARSED
        document.expected_paragraph_count = None
        document.extracted_paragraph_count = None
        document.needs_review = False
        document.promoted = False
        if raw_case.date is None and result.decision_date is not None:
            raw_case.date = result.decision_date
        raw_case.status = (
            RawCaseStatus.DISCOVERED
            if result.fetch_status == FetchStatus.SUCCESS
            else RawCaseStatus.FETCH_FAILED
        )

    def get_documents_pending_parse(
        self,
        limit: int | None,
    ) -> list[tuple[RawCase, RawCaseDocument]]:
        query = (
            select(RawCase, RawCaseDocument)
            .join(RawCaseDocument)
            .where(
                RawCaseDocument.fetch_status == FetchStatus.SUCCESS,
                RawCaseDocument.parse_status == ParseStatus.NOT_PARSED,
                RawCaseDocument.html.is_not(None),
            )
            .order_by(RawCase.date.desc().nulls_last())
        )
        if limit is not None:
            query = query.limit(limit)
        return list(self.session.execute(query).tuples())

    @staticmethod
    def save_parse_result(raw_case: RawCase, document: RawCaseDocument, result: DocumentParse) -> None:
        document.layout = result.layout
        document.parse_status = result.parse_status
        document.expected_paragraph_count = result.expected_paragraph_count
        document.extracted_paragraph_count = result.extracted_paragraph_count
        document.needs_review = result.needs_review

        if result.parse_status == ParseStatus.COMPLETE:
            raw_case.status = RawCaseStatus.COMPLETE
        elif result.parse_status == ParseStatus.INCOMPLETE:
            raw_case.status = RawCaseStatus.PARSE_INCOMPLETE
        else:
            raw_case.status = RawCaseStatus.NEEDS_REVIEW

    def get_documents_pending_promote(
        self,
        limit: int | None,
    ) -> list[tuple[RawCase, RawCaseDocument]]:
        query = (
            select(RawCase, RawCaseDocument)
            .join(RawCaseDocument)
            .where(
                RawCaseDocument.parse_status == ParseStatus.COMPLETE,
                RawCaseDocument.promoted.is_(False),
                RawCaseDocument.html.is_not(None),
                RawCaseDocument.layout.is_not(None),
            )
            .order_by(RawCase.date.desc().nulls_last())
        )
        if limit is not None:
            query = query.limit(limit)
        return list(self.session.execute(query).tuples())


class RawLegislationRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_existing_act_slugs(self, slugs: list[str]) -> set[str]:
        if not slugs:
            return set()
        stmt = select(RawAct.slug).where(RawAct.slug.in_(slugs))
        found = self.session.scalars(stmt)
        return set(found)

    def add_discovered_act(self, slug: str, title: str, source_url: str) -> None:
        self.session.add(
            RawAct(
                slug=slug,
                title=title,
                source_url=source_url,
                status=RawLegislationStatus.DISCOVERED,
            )
        )

    def get_acts(self, limit: int | None) -> list[RawAct]:
        stmt = select(RawAct).order_by(RawAct.slug)
        if limit is not None:
            stmt = stmt.limit(limit)
        found = self.session.scalars(stmt)
        return list(found)

    def get_existing_act_version_dates(self, raw_act_id: int) -> set[date]:
        stmt = select(RawActVersion.valid_from).where(RawActVersion.raw_act_id == raw_act_id)
        found = self.session.scalars(stmt)
        return set(found)

    def add_discovered_act_version(
        self,
        act: RawAct,
        valid_from: date,
        is_current: bool,
        source_url: str,
    ) -> None:
        if is_current:
            self.clear_current_act_versions(act.id)
        self.session.add(
            RawActVersion(
                raw_act_id=act.id,
                valid_from=valid_from,
                is_current=is_current,
                source_url=source_url,
                http_status=None,
                fetch_status=FetchStatus.NOT_FETCHED,
                fetch_error=None,
                html=None,
                source_metadata=None,
                parse_status=ParseStatus.NOT_PARSED,
                expected_provision_count=None,
                extracted_provision_count=None,
                needs_review=False,
                promoted=False,
            )
        )

    def clear_current_act_versions(self, raw_act_id: int) -> None:
        stmt = select(RawActVersion).where(
            RawActVersion.raw_act_id == raw_act_id,
            RawActVersion.is_current.is_(True),
        )
        found = self.session.scalars(stmt)
        for version in found:
            version.is_current = False

    def get_act_versions_pending_fetch(self, limit: int | None) -> list[RawActVersion]:
        stmt = (
            select(RawActVersion)
            .where(RawActVersion.fetch_status != FetchStatus.SUCCESS)
            .order_by(RawActVersion.is_current.desc(), RawActVersion.valid_from.desc())
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        found = self.session.scalars(stmt)
        return list(found)

    def save_act_version_fetch(self, version: RawActVersion, result: LegislationFetch) -> None:
        RawLegislationRepository.apply_fetch(version, result)
        act = self.session.get(RawAct, version.raw_act_id)
        if act is None:
            raise ValueError(f"No raw act for version {version.id}")
        RawLegislationRepository.apply_parent_status(act, result)

    def get_existing_subsidiary_slugs(self, slugs: list[str]) -> set[str]:
        if not slugs:
            return set()
        stmt = select(RawSubsidiaryLegislation.slug).where(
            RawSubsidiaryLegislation.slug.in_(slugs)
        )
        found = self.session.scalars(stmt)
        return set(found)

    def add_discovered_subsidiary_legislation(
        self,
        raw_act_id: int,
        slug: str,
        title: str,
        number: str,
        source_url: str,
    ) -> None:
        self.session.add(
            RawSubsidiaryLegislation(
                raw_act_id=raw_act_id,
                slug=slug,
                title=title,
                number=number,
                source_url=source_url,
                status=RawLegislationStatus.DISCOVERED,
            )
        )

    def get_subsidiary_legislations_for_acts(
        self,
        raw_act_ids: list[int],
    ) -> list[RawSubsidiaryLegislation]:
        if not raw_act_ids:
            return []
        stmt = (
            select(RawSubsidiaryLegislation)
            .where(RawSubsidiaryLegislation.raw_act_id.in_(raw_act_ids))
            .order_by(RawSubsidiaryLegislation.slug)
        )
        found = self.session.scalars(stmt)
        return list(found)

    def get_existing_subsidiary_version_dates(
        self,
        raw_subsidiary_legislation_id: int,
    ) -> set[date]:
        stmt = select(RawSubsidiaryLegislationVersion.valid_from).where(
            RawSubsidiaryLegislationVersion.raw_subsidiary_legislation_id
            == raw_subsidiary_legislation_id
        )
        found = self.session.scalars(stmt)
        return set(found)

    def add_discovered_subsidiary_legislation_version(
        self,
        instrument: RawSubsidiaryLegislation,
        valid_from: date,
        is_current: bool,
        source_url: str,
    ) -> None:
        if is_current:
            self.clear_current_subsidiary_versions(instrument.id)
        self.session.add(
            RawSubsidiaryLegislationVersion(
                raw_subsidiary_legislation_id=instrument.id,
                valid_from=valid_from,
                is_current=is_current,
                source_url=source_url,
                http_status=None,
                fetch_status=FetchStatus.NOT_FETCHED,
                fetch_error=None,
                html=None,
                source_metadata=None,
                parse_status=ParseStatus.NOT_PARSED,
                expected_provision_count=None,
                extracted_provision_count=None,
                needs_review=False,
                promoted=False,
            )
        )

    def clear_current_subsidiary_versions(self, raw_subsidiary_legislation_id: int) -> None:
        stmt = select(RawSubsidiaryLegislationVersion).where(
            RawSubsidiaryLegislationVersion.raw_subsidiary_legislation_id
            == raw_subsidiary_legislation_id,
            RawSubsidiaryLegislationVersion.is_current.is_(True),
        )
        found = self.session.scalars(stmt)
        for version in found:
            version.is_current = False

    def get_subsidiary_versions_pending_fetch(
        self,
        raw_subsidiary_legislation_ids: list[int],
        limit: int | None,
    ) -> list[RawSubsidiaryLegislationVersion]:
        if not raw_subsidiary_legislation_ids:
            return []
        stmt = (
            select(RawSubsidiaryLegislationVersion)
            .where(
                RawSubsidiaryLegislationVersion.raw_subsidiary_legislation_id.in_(
                    raw_subsidiary_legislation_ids
                ),
                RawSubsidiaryLegislationVersion.fetch_status != FetchStatus.SUCCESS,
            )
            .order_by(
                RawSubsidiaryLegislationVersion.is_current.desc(),
                RawSubsidiaryLegislationVersion.valid_from.desc(),
            )
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        found = self.session.scalars(stmt)
        return list(found)

    def save_subsidiary_version_fetch(
        self,
        version: RawSubsidiaryLegislationVersion,
        result: LegislationFetch,
    ) -> None:
        RawLegislationRepository.apply_fetch(version, result)
        instrument = self.session.get(
            RawSubsidiaryLegislation,
            version.raw_subsidiary_legislation_id,
        )
        if instrument is None:
            raise ValueError(f"No raw subsidiary legislation for version {version.id}")
        RawLegislationRepository.apply_parent_status(instrument, result)

    @staticmethod
    def apply_fetch(
        version: RawActVersion | RawSubsidiaryLegislationVersion,
        result: LegislationFetch,
    ) -> None:
        version.http_status = result.http_status
        version.fetch_status = result.fetch_status
        version.fetch_error = result.fetch_error
        version.html = result.html
        version.source_metadata = result.source_metadata
        version.parse_status = ParseStatus.NOT_PARSED
        version.expected_provision_count = result.expected_provision_count
        version.extracted_provision_count = result.extracted_provision_count
        version.needs_review = (
            result.fetch_status != FetchStatus.SUCCESS
            or result.expected_provision_count != result.extracted_provision_count
        )
        version.promoted = False

    @staticmethod
    def apply_parent_status(
        parent: RawAct | RawSubsidiaryLegislation,
        result: LegislationFetch,
    ) -> None:
        if result.fetch_status != FetchStatus.SUCCESS:
            parent.status = RawLegislationStatus.FETCH_FAILED
            return
        if result.expected_provision_count != result.extracted_provision_count:
            parent.status = RawLegislationStatus.NEEDS_REVIEW

    def get_act_versions_pending_parse(self, limit: int | None) -> list[tuple[RawAct, RawActVersion]]:
        stmt = (
            select(RawAct, RawActVersion)
            .join(RawActVersion, RawActVersion.raw_act_id == RawAct.id)
            .where(
                RawActVersion.fetch_status == FetchStatus.SUCCESS,
                RawActVersion.parse_status == ParseStatus.NOT_PARSED,
                RawActVersion.html.is_not(None),
            )
            .order_by(RawActVersion.is_current.desc(), RawActVersion.valid_from.desc())
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        return list(self.session.execute(stmt).tuples())

    def save_act_version_parse(
        self,
        act: RawAct,
        version: RawActVersion,
        result: LegislationParse,
    ) -> None:
        version.parse_status = result.parse_status
        version.expected_provision_count = result.expected_provision_count
        version.extracted_provision_count = result.extracted_provision_count
        version.needs_review = result.needs_review
        self.refresh_act_status(act)

    def get_act_versions_pending_promote(
        self,
        limit: int | None,
    ) -> list[tuple[RawAct, RawActVersion]]:
        stmt = (
            select(RawAct, RawActVersion)
            .join(RawActVersion, RawActVersion.raw_act_id == RawAct.id)
            .where(
                RawActVersion.parse_status == ParseStatus.COMPLETE,
                RawActVersion.promoted.is_(False),
                RawActVersion.html.is_not(None),
            )
            .order_by(RawActVersion.is_current.desc(), RawActVersion.valid_from.desc())
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        return list(self.session.execute(stmt).tuples())

    @staticmethod
    def mark_act_version_promoted(version: RawActVersion) -> None:
        version.promoted = True

    def get_subsidiary_versions_pending_parse(
        self,
        limit: int | None,
    ) -> list[tuple[RawSubsidiaryLegislation, RawSubsidiaryLegislationVersion]]:
        stmt = (
            select(RawSubsidiaryLegislation, RawSubsidiaryLegislationVersion)
            .join(
                RawSubsidiaryLegislationVersion,
                RawSubsidiaryLegislationVersion.raw_subsidiary_legislation_id
                == RawSubsidiaryLegislation.id,
            )
            .where(
                RawSubsidiaryLegislationVersion.fetch_status == FetchStatus.SUCCESS,
                RawSubsidiaryLegislationVersion.parse_status == ParseStatus.NOT_PARSED,
                RawSubsidiaryLegislationVersion.html.is_not(None),
            )
            .order_by(
                RawSubsidiaryLegislationVersion.is_current.desc(),
                RawSubsidiaryLegislationVersion.valid_from.desc(),
            )
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        return list(self.session.execute(stmt).tuples())

    def save_subsidiary_version_parse(
        self,
        instrument: RawSubsidiaryLegislation,
        version: RawSubsidiaryLegislationVersion,
        result: LegislationParse,
    ) -> None:
        version.parse_status = result.parse_status
        version.expected_provision_count = result.expected_provision_count
        version.extracted_provision_count = result.extracted_provision_count
        version.needs_review = result.needs_review
        self.refresh_subsidiary_status(instrument)

    def get_subsidiary_versions_pending_promote(
        self,
        limit: int | None,
    ) -> list[tuple[RawAct, RawSubsidiaryLegislation, RawSubsidiaryLegislationVersion]]:
        stmt = (
            select(RawAct, RawSubsidiaryLegislation, RawSubsidiaryLegislationVersion)
            .join(
                RawSubsidiaryLegislation,
                RawSubsidiaryLegislation.raw_act_id == RawAct.id,
            )
            .join(
                RawSubsidiaryLegislationVersion,
                RawSubsidiaryLegislationVersion.raw_subsidiary_legislation_id
                == RawSubsidiaryLegislation.id,
            )
            .where(
                RawSubsidiaryLegislationVersion.parse_status == ParseStatus.COMPLETE,
                RawSubsidiaryLegislationVersion.promoted.is_(False),
                RawSubsidiaryLegislationVersion.is_current.is_(True),
                RawSubsidiaryLegislationVersion.html.is_not(None),
            )
            .order_by(RawSubsidiaryLegislation.slug)
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        return list(self.session.execute(stmt).tuples())

    @staticmethod
    def mark_subsidiary_version_promoted(version: RawSubsidiaryLegislationVersion) -> None:
        version.promoted = True

    def refresh_act_status(self, act: RawAct) -> None:
        stmt = select(RawActVersion).where(RawActVersion.raw_act_id == act.id)
        found = self.session.scalars(stmt)
        act.status = RawLegislationRepository.parent_status_from_versions(list(found))

    def refresh_subsidiary_status(self, instrument: RawSubsidiaryLegislation) -> None:
        stmt = select(RawSubsidiaryLegislationVersion).where(
            RawSubsidiaryLegislationVersion.raw_subsidiary_legislation_id == instrument.id
        )
        found = self.session.scalars(stmt)
        instrument.status = RawLegislationRepository.parent_status_from_versions(list(found))

    @staticmethod
    def parent_status_from_versions(
        versions: Sequence[RawActVersion | RawSubsidiaryLegislationVersion],
    ) -> RawLegislationStatus:
        if not versions:
            return RawLegislationStatus.DISCOVERED

        statuses = [version.parse_status for version in versions]
        fetch_failed = any(version.fetch_status != FetchStatus.SUCCESS for version in versions)

        if any(status == ParseStatus.NOT_PARSED for status in statuses):
            if fetch_failed:
                return RawLegislationStatus.FETCH_FAILED
            return RawLegislationStatus.DISCOVERED
        if any(status == ParseStatus.INCOMPLETE for status in statuses):
            return RawLegislationStatus.PARSE_INCOMPLETE
        if all(status == ParseStatus.COMPLETE for status in statuses):
            return RawLegislationStatus.COMPLETE
        return RawLegislationStatus.NEEDS_REVIEW
