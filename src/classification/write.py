from src.classification.classify import Classification, Classifier
from src.classification.schema import ClassificationLimits
from src.database import get_knowledge_base_session_maker
from src.embeddings.embed import KnowledgeEmbedder
from src.knowledge.models.paragraphs import Paragraph
from src.knowledge.models.provisions import Provision
from src.knowledge.models.taxonomy import Concept, FunctionalRole, FunctionalRoleAppliesTo, Topic
from src.knowledge.repository import (
    KnowledgeCaseRepository,
    KnowledgeLegislationRepository,
    KnowledgeTaxonomyRepository,
)
from src.logger import get_logger
from src.notify.schema import StageReport, StageReporter, emit_report

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
        limits: ClassificationLimits | None = None,
    ) -> StageReport:
        classifier = Classifier.load("judgments")
        limits = limits or ClassificationLimits()
        session_maker = get_knowledge_base_session_maker()

        with session_maker() as session:
            total_pending = KnowledgeCaseRepository(session).count_paragraphs_pending_classification()
        total = total_pending if max_paragraphs is None else min(total_pending, max_paragraphs)
        logger.info(
            "Classifying %s paragraphs in batches of %s",
            total,
            limits.batch_size,
        )

        classified = 0
        skipped_empty = 0
        after_id: int | None = None
        while max_paragraphs is None or classified < max_paragraphs:
            with session_maker() as session:
                case_repository = KnowledgeCaseRepository(session)
                taxonomy = self.load_taxonomy(KnowledgeTaxonomyRepository(session))
                pending = case_repository.get_paragraphs_pending_classification(
                    limits.batch_size,
                    after_id,
                )
                if not pending:
                    break

                after_id = pending[-1].id
                nonempty: list[Paragraph] = []
                for paragraph in pending:
                    if paragraph.content.strip():
                        nonempty.append(paragraph)
                    else:
                        skipped_empty += 1

                if max_paragraphs is not None:
                    nonempty = nonempty[: max_paragraphs - classified]
                if nonempty:
                    logger.info(
                        "Classifying paragraph ids %s–%s (%s texts, %s done of %s)",
                        nonempty[0].id,
                        nonempty[-1].id,
                        len(nonempty),
                        classified,
                        total,
                    )
                    results = classifier.classify_many([paragraph.content for paragraph in nonempty])
                    for paragraph, result in zip(nonempty, results, strict=True):
                        role_id, topic_ids, concept_ids = self.label_ids(
                            taxonomy,
                            result,
                            FunctionalRoleAppliesTo.CASE,
                        )
                        case_repository.save_paragraph_classification(
                            paragraph,
                            role_id,
                            topic_ids,
                            concept_ids,
                        )
                    classified += len(nonempty)
                    session.commit()
                    logger.info("Classified %s of %s paragraphs", classified, total)
                    if classified == len(nonempty) or classified % limits.progress_every == 0:
                        emit_report(
                            reporter,
                            StageReport(
                                stage="classify",
                                counts={"classified": classified},
                                in_progress=True,
                                done=classified,
                                total=total,
                            ),
                        )

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
