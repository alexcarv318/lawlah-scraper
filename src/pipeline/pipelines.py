from src.aliases.extract import CaseAliasExtractor
from src.cases.parse import CaseRawParser
from src.cases.promote import CaseRawPromoter
from src.cases.scrape import CaseRawScraper
from src.classification.write import KnowledgeClassifier
from src.embeddings.embed import KnowledgeEmbedder
from src.legislation.parse import LegislationRawParser
from src.legislation.promote import LegislationRawPromoter
from src.legislation.scrape import LegislationRawScraper
from src.logger import get_logger
from src.references.extract import CaseReferenceExtractor
from src.references.resolve import ReferenceResolver

logger = get_logger(__name__)


class CasePipeline:
    @staticmethod
    def backfill(
        max_search_pages: int | None = None,
        max_documents: int | None = None,
        max_cases: int | None = None,
    ) -> None:
        logger.info("Case backfill started")
        CaseRawScraper().run(
            max_search_pages=max_search_pages,
            max_documents=max_documents,
            until_latest_stored_date=False,
        )
        CaseRawParser().run()
        CaseRawPromoter().run(max_cases)
        logger.info("Case backfill finished")

    @staticmethod
    def update(
        max_search_pages: int | None = None,
        max_documents: int | None = None,
        max_cases: int | None = None,
    ) -> None:
        logger.info("Case update started")
        CaseRawScraper().run(
            max_search_pages=max_search_pages,
            max_documents=max_documents,
            until_latest_stored_date=True,
        )
        CaseRawParser().run()
        CaseRawPromoter().run(max_cases)
        logger.info("Case update finished")


class ParagraphPipeline:
    @staticmethod
    def run(
        max_paragraphs: int | None = None,
        max_cases: int | None = None,
    ) -> None:
        logger.info("Paragraph pipeline started")
        KnowledgeClassifier().classify_paragraphs(max_paragraphs)
        CaseAliasExtractor().run(max_cases)
        CaseReferenceExtractor().run(max_cases)
        ReferenceResolver().run()
        KnowledgeEmbedder().run(max_paragraphs=max_paragraphs, max_provisions=0)
        logger.info("Paragraph pipeline finished")


class ActPipeline:
    @staticmethod
    def backfill(
        max_acts: int | None = None,
        max_versions: int | None = None,
        max_provisions: int | None = None,
    ) -> None:
        logger.info("Act backfill started")
        LegislationRawScraper().run(max_acts=max_acts, max_versions=max_versions)
        LegislationRawParser().run(max_versions=max_versions)
        LegislationRawPromoter().run(max_versions=max_versions, include_subsidiary=False)
        KnowledgeClassifier().classify_provisions(max_provisions)
        KnowledgeEmbedder().run(max_paragraphs=0, max_provisions=max_provisions)
        logger.info("Act backfill finished")
