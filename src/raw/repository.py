from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.cases.schema import DocumentFetch, DocumentParse
from src.raw.models.cases import (
    FetchStatus,
    ParseStatus,
    RawCase,
    RawCaseDocument,
    RawCaseStatus,
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
