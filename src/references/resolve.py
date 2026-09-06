import re

from src.database import get_knowledge_base_session_maker
from src.knowledge.models.aliases import Alias
from src.knowledge.models.cases import Case
from src.knowledge.models.paragraphs import Paragraph
from src.knowledge.models.references import CitationKind, Reference
from src.knowledge.repository import KnowledgeCaseRepository
from src.logger import get_logger
from src.notify.schema import StageReport, StageReporter, emit_report

logger = get_logger(__name__)


class ReferenceResolver:
    @staticmethod
    def run(
        max_references: int | None = None,
        reporter: StageReporter | None = None,
    ) -> StageReport:
        session_maker = get_knowledge_base_session_maker()
        with session_maker() as session:
            repository = KnowledgeCaseRepository(session)
            unresolved = repository.get_unresolved_references(max_references)
            resolved = ReferenceResolver.resolve_rows(repository, unresolved)
            session.commit()

        logger.info("Resolved %s of %s references", resolved, len(unresolved))
        report = StageReport(
            stage="resolve",
            counts={"resolved": resolved, "still unresolved": len(unresolved) - resolved},
        )

        emit_report(reporter, report)

        return report

    @staticmethod
    def resolve_rows(
        repository: KnowledgeCaseRepository,
        unresolved: list[Reference],
    ) -> int:
        if not unresolved:
            return 0

        paragraphs = repository.get_paragraphs_by_ids(
            [reference.source_paragraph_id for reference in unresolved]
        )
        aliases = repository.get_aliases_by_ids(
            [
                reference.alias_id
                for reference in unresolved
                if reference.alias_id is not None
            ]
        )
        citations = ReferenceResolver.citations_to_lookup(unresolved, aliases)
        cases = repository.get_cases_by_neutral_citations(citations)
        case_ids = {paragraph.case_id for paragraph in paragraphs.values()}
        case_ids.update(case.id for case in cases.values())
        by_ordinal = repository.get_paragraphs_by_case_ordinal(list(case_ids))

        resolved = 0
        for reference in unresolved:
            source = paragraphs.get(reference.source_paragraph_id)
            if source is None:
                continue
            alias = aliases.get(reference.alias_id) if reference.alias_id is not None else None
            if ReferenceResolver.resolve_one(reference, source, alias, cases, by_ordinal):
                resolved += 1
        return resolved

    @staticmethod
    def resolve_one(
        reference: Reference,
        source: Paragraph,
        alias: Alias | None,
        cases: dict[str, Case],
        by_ordinal: dict[tuple[int, int], Paragraph],
    ) -> bool:
        if reference.kind == CitationKind.SELF:
            return ReferenceResolver.resolve_self(reference, source, by_ordinal)
        if reference.kind != CitationKind.CASE:
            return False

        citation = reference.neutral_citation
        if citation is None:
            citation = ReferenceResolver.neutral_citation_from_alias(alias)
        if citation is None:
            return False

        case = cases.get(citation)
        if case is None:
            return False

        pins = reference.paragraph_pins
        if len(pins) == 1:
            paragraph = by_ordinal.get((case.id, pins[0]))
            if paragraph is not None:
                reference.target_paragraph_id = paragraph.id
                return True
        reference.target_case_id = case.id
        return True

    @staticmethod
    def resolve_self(
        reference: Reference,
        source: Paragraph,
        by_ordinal: dict[tuple[int, int], Paragraph],
    ) -> bool:
        if not reference.paragraph_pins:
            return False
        paragraph = by_ordinal.get((source.case_id, reference.paragraph_pins[0]))
        if paragraph is None:
            return False
        reference.target_paragraph_id = paragraph.id
        return True

    @staticmethod
    def citations_to_lookup(
        unresolved: list[Reference],
        aliases: dict[int, Alias],
    ) -> list[str]:
        citations: list[str] = []
        for reference in unresolved:
            if reference.neutral_citation is not None:
                citations.append(reference.neutral_citation)
                continue
            alias = aliases.get(reference.alias_id) if reference.alias_id is not None else None
            citation = ReferenceResolver.neutral_citation_from_alias(alias)
            if citation is not None:
                citations.append(citation)
        return citations

    @staticmethod
    def neutral_citation_from_alias(alias: Alias | None) -> str | None:
        if alias is None:
            return None
        match = re.search(
            r"\[[12]\d{3}\]\s+SG(?:HC(?:[RF]|\([A-Z]+\))?|CA(?:\([A-Z]+\))?|DC|FC)\s+\d+",
            alias.expanded_text,
        )
        return match.group(0) if match is not None else None
