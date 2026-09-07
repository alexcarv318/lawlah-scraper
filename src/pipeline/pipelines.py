from src.aliases.extract import CaseAliasExtractor
from src.cases.parse import CaseRawParser
from src.cases.promote import CaseRawPromoter
from src.cases.scrape import CaseRawScraper
from src.classification.service import KnowledgeClassifier
from src.embeddings.embed import KnowledgeEmbedder
from src.legislation.parse import LegislationRawParser
from src.legislation.promote import LegislationRawPromoter
from src.legislation.scrape import LegislationRawScraper
from src.logger import get_logger
from src.notify.schema import StageReporter
from src.notify.service import run_job
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
        def action(reporter: StageReporter | None) -> None:
            logger.info("Case backfill started")

            CaseRawScraper().run(
                max_search_pages=max_search_pages,
                max_documents=max_documents,
                until_latest_stored_date=False,
                reporter=reporter,
            )
            CaseRawParser().run(reporter=reporter)
            CaseRawPromoter().run(max_cases, reporter)

            logger.info("Case backfill finished")

        run_job("Cases backfill", action)

    @staticmethod
    def update(
        max_search_pages: int | None = None,
        max_documents: int | None = None,
        max_cases: int | None = None,
    ) -> None:
        def action(reporter: StageReporter | None) -> None:
            logger.info("Case update started")

            CaseRawScraper().run(
                max_search_pages=max_search_pages,
                max_documents=max_documents,
                until_latest_stored_date=True,
                reporter=reporter,
            )
            CaseRawParser().run(reporter=reporter)
            CaseRawPromoter().run(max_cases, reporter)

            logger.info("Case update finished")

        run_job("Cases update", action)


class ParagraphPipeline:
    @staticmethod
    def run(
        max_paragraphs: int | None = None,
        max_cases: int | None = None,
    ) -> None:
        def action(reporter: StageReporter | None) -> None:
            logger.info("Paragraph pipeline started")

            KnowledgeClassifier().classify_paragraphs(max_paragraphs, reporter)
            CaseAliasExtractor().run(max_cases, reporter)
            CaseReferenceExtractor().run(max_cases, reporter)
            ReferenceResolver.run(reporter=reporter)
            KnowledgeEmbedder().run(
                max_paragraphs=max_paragraphs,
                max_provisions=0,
                reporter=reporter,
            )

            logger.info("Paragraph pipeline finished")

        run_job("Paragraphs", action)


class ActPipeline:
    @staticmethod
    def backfill(
        max_acts: int | None = None,
        max_versions: int | None = None,
        max_provisions: int | None = None,
    ) -> None:
        def action(reporter: StageReporter | None) -> None:
            logger.info("Act backfill started")

            LegislationRawScraper().run(
                max_acts=max_acts,
                max_versions=max_versions,
                reporter=reporter,
            )
            LegislationRawParser().run(max_versions=max_versions, reporter=reporter)
            LegislationRawPromoter().run(
                max_versions=max_versions,
                include_subsidiary=False,
                reporter=reporter,
            )
            KnowledgeClassifier().classify_provisions(max_provisions, reporter)
            KnowledgeEmbedder().run(
                max_paragraphs=0,
                max_provisions=max_provisions,
                reporter=reporter,
            )

            logger.info("Act backfill finished")

        run_job("Acts backfill", action)
