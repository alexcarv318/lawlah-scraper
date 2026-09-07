from src.classification.classify import Classification, Classifier
from src.database import get_knowledge_base_session_maker
from src.embeddings.embed import KnowledgeEmbedder
from src.knowledge.models.provisions import Provision
from src.knowledge.models.taxonomy import Concept, FunctionalRole, FunctionalRoleAppliesTo, Topic
from src.knowledge.repository import (
    KnowledgeCaseRepository,
    KnowledgeLegislationRepository,
    KnowledgeTaxonomyRepository,
)
from src.logger import get_logger
from src.notify.schema import StageReport, StageReporter, emit_report

COMMIT_EVERY = 32
CLASSIFY_PROGRESS_EVERY = 200

logger = get_logger(__name__)


class TaxonomyLookup:
    def __init__(
        self,
        roles: dict[str, FunctionalRole],
        topics: dict[str, Topic],
        concepts: dict[str, Concept],
    ) -> None:
        self.roles = {name.casefold(): role for name, role in roles.items()}
        self.topics = {name.casefold(): topic for name, topic in topics.items()}
        self.concepts = {name.casefold(): concept for name, concept in concepts.items()}

    def role_id(self, name: str, applies_to: FunctionalRoleAppliesTo) -> int:
        role = self.roles.get(name.casefold())
        if role is None:
            raise ValueError(f"Unknown functional role {name!r}")
        if role.applies_to != applies_to:
            raise ValueError(
                f"Role {name!r} applies to {role.applies_to.value}, expected {applies_to.value}"
            )
        return role.id

    def topic_ids(self, names: tuple[str, ...]) -> list[int]:
        ids: list[int] = []
        for name in names:
            topic = self.topics.get(name.casefold())
            if topic is None:
                raise ValueError(f"Unknown topic {name!r}")
            ids.append(topic.id)
        return ids

    def concept_ids(self, names: tuple[str, ...]) -> list[int]:
        ids: list[int] = []
        for name in names:
            concept = self.concepts.get(name.casefold())
            if concept is None:
                raise ValueError(f"Unknown concept {name!r}")
            ids.append(concept.id)
        return ids


class KnowledgeClassifier:
    @staticmethod
    def load_taxonomy(repository: KnowledgeTaxonomyRepository) -> TaxonomyLookup:
        return TaxonomyLookup(
            roles=repository.get_roles(),
            topics=repository.get_topics(),
            concepts=repository.get_concepts(),
        )

    @staticmethod
    def label_ids(
        taxonomy: TaxonomyLookup,
        result: Classification,
        applies_to: FunctionalRoleAppliesTo,
    ) -> tuple[int, list[int], list[int]]:
        return (
            taxonomy.role_id(result.role, applies_to),
            taxonomy.topic_ids(result.topics),
            taxonomy.concept_ids(result.concepts),
        )

    def classify_paragraphs(
        self,
        max_paragraphs: int | None = None,
        reporter: StageReporter | None = None,
    ) -> StageReport:
        classifier = Classifier.load("judgments")
        session_maker = get_knowledge_base_session_maker()

        with session_maker() as session:
            case_repository = KnowledgeCaseRepository(session)
            taxonomy = self.load_taxonomy(KnowledgeTaxonomyRepository(session))
            pending = case_repository.get_paragraphs_pending_classification(max_paragraphs)
            logger.info("Classifying %s paragraphs", len(pending))

            classified = 0
            skipped_empty = 0
            for paragraph in pending:
                if not paragraph.content.strip():
                    skipped_empty += 1
                    continue
                role_id, topic_ids, concept_ids = self.label_ids(
                    taxonomy,
                    classifier.classify(paragraph.content),
                    FunctionalRoleAppliesTo.CASE,
                )
                case_repository.save_paragraph_classification(
                    paragraph,
                    role_id,
                    topic_ids,
                    concept_ids,
                )
                classified += 1
                if classified % COMMIT_EVERY == 0:
                    session.commit()
                    logger.info("Classified %s of %s paragraphs", classified, len(pending))
                if classified % CLASSIFY_PROGRESS_EVERY == 0:

                    emit_report(
                        reporter,
                        StageReport(
                            stage="classify",
                            counts={"classified": classified},
                            in_progress=True,
                            done=classified,
                            total=len(pending),
                        ),
                    )

            session.commit()

        logger.info("Paragraph classification finished: %s paragraphs", classified)
        report = StageReport(
            stage="classify",
            counts={"classified": classified, "empty skipped": skipped_empty},
        )

        emit_report(reporter, report)

        return report

    def classify_provisions(
        self,
        max_provisions: int | None = None,
        reporter: StageReporter | None = None,
    ) -> StageReport:
        classifier = Classifier.load("legislation")
        session_maker = get_knowledge_base_session_maker()

        with session_maker() as session:
            legislation_repository = KnowledgeLegislationRepository(session)
            taxonomy = self.load_taxonomy(KnowledgeTaxonomyRepository(session))
            trees = legislation_repository.get_current_provision_trees()
            logger.info("Classifying current act sections across %s documents", len(trees))

            classified = 0
            for nodes in trees:
                if not nodes or nodes[0].act_version_id is None:
                    continue
                classified += self.classify_document_sections(
                    classifier,
                    legislation_repository,
                    taxonomy,
                    nodes,
                    remaining=None if max_provisions is None else max_provisions - classified,
                )
                session.commit()
                if max_provisions is not None and classified >= max_provisions:
                    break

        logger.info("Provision classification finished: %s sections", classified)
        report = StageReport(
            stage="classify",
            counts={"classified sections": classified, "documents": len(trees)},
        )

        emit_report(reporter, report)

        return report

    def classify_document_sections(
        self,
        classifier: Classifier,
        repository: KnowledgeLegislationRepository,
        taxonomy: TaxonomyLookup,
        nodes: list[Provision],
        remaining: int | None,
    ) -> int:
        by_id = {node.id: node for node in nodes}
        previous_text = ""
        classified = 0
        sections = KnowledgeEmbedder.section_roots(nodes, by_id)
        sections.sort(key=lambda section: section.ordinal)

        for section in sections:
            text = KnowledgeEmbedder.render_subtree(section, nodes)
            if section.functional_role_id is not None or not text.strip():
                previous_text = text
                continue
            if remaining is not None and classified >= remaining:
                return classified

            role_id, topic_ids, concept_ids = self.label_ids(
                taxonomy,
                classifier.classify(text, previous_text=previous_text),
                FunctionalRoleAppliesTo.LEGISLATION,
            )
            repository.save_provision_classification(section, role_id, topic_ids, concept_ids)
            previous_text = text
            classified += 1

        return classified
