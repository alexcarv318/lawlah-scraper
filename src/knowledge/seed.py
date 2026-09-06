import torch

from src.classification.training.concept_topics import OFFICIAL_TOPIC_CONCEPTS, official_topic_name
from src.classification.training.labels import (
    CHECKPOINT_ROOT,
    DATASET_ROOT,
    SOURCES,
    build_label_scheme,
    load_splits,
    scheme_from_payload,
)
from src.classification.training.model import read_checkpoint
from src.database import get_knowledge_base_session_maker
from src.knowledge.models.taxonomy import FunctionalRoleAppliesTo
from src.knowledge.repository import KnowledgeTaxonomyRepository
from src.logger import get_logger

SOURCE_APPLIES_TO = {
    "judgments": FunctionalRoleAppliesTo.CASE,
    "legislation": FunctionalRoleAppliesTo.LEGISLATION,
}

logger = get_logger(__name__)


def load_roles(source: str) -> tuple[str, ...]:
    checkpoint = CHECKPOINT_ROOT / source / "role" / "best.pt"
    if checkpoint.exists():
        payload = read_checkpoint(checkpoint, torch.device("cpu"))
        scheme = scheme_from_payload(payload["scheme"])
        logger.info("Loaded %s roles from %s", source, checkpoint)
        return scheme.roles

    dataset = DATASET_ROOT / SOURCES[source] / "train.parquet"
    if dataset.exists():
        logger.info("Loaded %s roles from %s", source, dataset)
        return build_label_scheme(load_splits(source)).roles

    raise FileNotFoundError(
        f"No {source} role checkpoint at {checkpoint} and no dataset at {dataset}"
    )


def seed_topics_and_concepts(repository: KnowledgeTaxonomyRepository) -> tuple[int, int]:
    added_topics = 0
    added_concepts = 0
    for topic_key, concept_names in OFFICIAL_TOPIC_CONCEPTS.items():
        topic, created_topic = repository.ensure_topic(official_topic_name(topic_key))
        if created_topic:
            added_topics += 1
        for concept_name in concept_names:
            _, created_concept = repository.ensure_concept(concept_name, topic.id)
            if created_concept:
                added_concepts += 1
    return added_topics, added_concepts


def seed_roles(repository: KnowledgeTaxonomyRepository) -> int:
    added = 0
    for source, applies_to in SOURCE_APPLIES_TO.items():
        for name in load_roles(source):
            _, created = repository.ensure_role(name, applies_to)
            if created:
                added += 1
    return added


def seed() -> None:
    session_maker = get_knowledge_base_session_maker()
    with session_maker() as session:
        repository = KnowledgeTaxonomyRepository(session)
        added_topics, added_concepts = seed_topics_and_concepts(repository)
        added_roles = seed_roles(repository)
        session.commit()

    logger.info(
        "Taxonomy seed finished: %s new topics, %s new concepts, %s new roles",
        added_topics,
        added_concepts,
        added_roles,
    )


def main() -> None:
    seed()


if __name__ == "__main__":
    main()
