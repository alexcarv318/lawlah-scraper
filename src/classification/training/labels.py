from dataclasses import dataclass
from pathlib import Path

from datasets import DatasetDict, load_dataset  # type: ignore[import-untyped]

from src.classification.training.concept_topics import official_concept_home

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
DATASET_ROOT = PACKAGE_ROOT / "datasets"
CHECKPOINT_ROOT = PACKAGE_ROOT / "checkpoints"

SOURCES = {
    "judgments": "singapore-judgments-paragraph-classification",
    "legislation": "singapore-legislation-provision-classification",
}

TASKS = ("role", "topics", "concepts")
DEFAULT_ENCODER = "microsoft/deberta-v3-small"
ROLE_ENCODER = "microsoft/deberta-v3-base"
DEFAULT_EPOCHS = {
    "role": 3,
    "topics": 5,
    "concepts": 5,
}


@dataclass(frozen=True)
class LabelScheme:
    roles: tuple[str, ...]
    topics: tuple[str, ...]
    concepts: tuple[str, ...]
    role_ids: dict[str, int]
    topic_ids: dict[str, int]
    concept_ids: dict[str, int]
    concept_topic_ids: tuple[int, ...]
    concept_allowed_topic_ids: tuple[frozenset[int], ...] = ()

    def __setstate__(self, state: dict[str, object]) -> None:
        object.__setattr__(self, "roles", state["roles"])
        object.__setattr__(self, "topics", state["topics"])
        object.__setattr__(self, "concepts", state["concepts"])
        object.__setattr__(self, "role_ids", state["role_ids"])
        object.__setattr__(self, "topic_ids", state["topic_ids"])
        object.__setattr__(self, "concept_ids", state["concept_ids"])
        object.__setattr__(self, "concept_topic_ids", state["concept_topic_ids"])
        allowed = state.get("concept_allowed_topic_ids", ())
        if not isinstance(allowed, tuple) or not allowed:
            converted: list[frozenset[int]] = []
            topic_ids = state["concept_topic_ids"]
            if not isinstance(topic_ids, tuple):
                raise TypeError("Checkpoint scheme is missing concept_topic_ids")

            for topic_id in topic_ids:
                if int(topic_id) < 0:
                    converted.append(frozenset())
                else:
                    converted.append(frozenset([int(topic_id)]))

            allowed = tuple(converted)

        object.__setattr__(self, "concept_allowed_topic_ids", allowed)

    def role_id(self, role: str | None) -> int:
        if role is None:
            return -100

        return self.role_ids.get(role, -100)

    def topic_targets(self, names: list[str]) -> list[float]:
        targets = [0.0] * len(self.topics)
        for name in names:
            topic_id = self.topic_ids.get(name)
            if topic_id is not None:
                targets[topic_id] = 1.0

        return targets

    def concept_targets(self, names: list[str]) -> list[float]:
        targets = [0.0] * len(self.concepts)
        for name in names:
            concept_id = self.concept_ids.get(name)
            if concept_id is not None:
                targets[concept_id] = 1.0

        return targets

    def concept_mask(self, topic_targets: list[float]) -> list[float]:
        active: set[int] = set()
        for topic_id, flag in enumerate(topic_targets):
            if flag == 1.0:
                active.add(topic_id)

        mask = [0.0] * len(self.concepts)
        for concept_id, allowed in enumerate(self.concept_allowed_topic_ids):
            if allowed and not allowed.isdisjoint(active):
                mask[concept_id] = 1.0

        return mask

    def decode_topics(self, flags: list[float]) -> tuple[str, ...]:
        names: list[str] = []
        for name, flag in zip(self.topics, flags, strict=True):
            if flag:
                names.append(name)

        return tuple(names)

    def decode_concepts(self, flags: list[float]) -> tuple[str, ...]:
        names: list[str] = []
        for name, flag in zip(self.concepts, flags, strict=True):
            if flag:
                names.append(name)

        return tuple(names)


def concept_input(text: str, topics: list[str]) -> str:
    if not topics:
        return text

    return "Topics: " + "; ".join(topics) + "\n\n" + text


def role_input(text: str, previous_text: str) -> str:
    if not previous_text:
        return text

    return text + "\n\nPrevious: " + previous_text


def load_splits(source: str) -> DatasetDict:
    directory = DATASET_ROOT / SOURCES[source]
    loaded = load_dataset(
        "parquet",
        data_files={
            "train": str(directory / "train.parquet"),
            "validation": str(directory / "validation.parquet"),
            "test": str(directory / "test.parquet"),
        },
    )
    if not isinstance(loaded, DatasetDict):
        raise TypeError("Expected a DatasetDict of train/validation/test splits")

    return loaded


def identity_ids(names: tuple[str, ...]) -> dict[str, int]:
    ids: dict[str, int] = {}
    for index, name in enumerate(names):
        ids[name] = index

    return ids


def apply_official_concept_topics(scheme: LabelScheme) -> LabelScheme:
    homes = official_concept_home()
    allowlists: list[frozenset[int]] = []
    legacy_ids: list[int] = []

    for concept_name in scheme.concepts:
        topic_name = homes.get(concept_name.casefold())
        topic_id = scheme.topic_ids.get(topic_name) if topic_name is not None else None
        if topic_id is None:
            allowlists.append(frozenset())
            legacy_ids.append(-1)
            continue

        allowlists.append(frozenset([topic_id]))
        legacy_ids.append(topic_id)

    return LabelScheme(
        roles=scheme.roles,
        topics=scheme.topics,
        concepts=scheme.concepts,
        role_ids=scheme.role_ids,
        topic_ids=scheme.topic_ids,
        concept_ids=scheme.concept_ids,
        concept_topic_ids=tuple(legacy_ids),
        concept_allowed_topic_ids=tuple(allowlists),
    )


def build_label_scheme(splits: DatasetDict) -> LabelScheme:
    roles: set[str] = set()
    topics: set[str] = set()
    concepts: set[str] = set()

    for split in splits.values():
        for role, row_topics, row_concepts in zip(split["role"], split["topics"], split["concepts"], strict=True):
            if isinstance(role, str):
                roles.add(role)

            topics.update(row_topics)
            concepts.update(row_concepts)

    role_names = tuple(sorted(roles))
    topic_names = tuple(sorted(topics))
    concept_names = tuple(sorted(concepts))
    return apply_official_concept_topics(
        LabelScheme(
            roles=role_names,
            topics=topic_names,
            concepts=concept_names,
            role_ids=identity_ids(role_names),
            topic_ids=identity_ids(topic_names),
            concept_ids=identity_ids(concept_names),
            concept_topic_ids=tuple([-1] * len(concept_names)),
            concept_allowed_topic_ids=tuple([frozenset() for _ in concept_names]),
        )
    )


def align_scheme(checkpoint: LabelScheme, rebuilt: LabelScheme) -> LabelScheme:
    allowlists: list[frozenset[int]] = []
    legacy_ids: list[int] = []

    for concept_name in checkpoint.concepts:
        rebuilt_concept_id = rebuilt.concept_ids.get(concept_name)
        if rebuilt_concept_id is None:
            allowlists.append(frozenset())
            legacy_ids.append(-1)
            continue

        checkpoint_ids: set[int] = set()
        for rebuilt_topic_id in rebuilt.concept_allowed_topic_ids[rebuilt_concept_id]:
            topic_name = rebuilt.topics[rebuilt_topic_id]
            checkpoint_topic_id = checkpoint.topic_ids.get(topic_name)
            if checkpoint_topic_id is not None:
                checkpoint_ids.add(checkpoint_topic_id)

        allowlists.append(frozenset(checkpoint_ids))
        if checkpoint_ids:
            legacy_ids.append(next(iter(checkpoint_ids)))
        else:
            legacy_ids.append(-1)

    return apply_official_concept_topics(
        LabelScheme(
            roles=checkpoint.roles,
            topics=checkpoint.topics,
            concepts=checkpoint.concepts,
            role_ids=checkpoint.role_ids,
            topic_ids=checkpoint.topic_ids,
            concept_ids=checkpoint.concept_ids,
            concept_topic_ids=tuple(legacy_ids),
            concept_allowed_topic_ids=tuple(allowlists),
        )
    )


def scheme_for_source(checkpoint: LabelScheme, source: str) -> LabelScheme:
    return align_scheme(checkpoint, build_label_scheme(load_splits(source)))


def string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) and not isinstance(value, tuple):
        raise TypeError("Expected a list of label names")

    names: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise TypeError("Expected string labels")

        names.append(item)

    return tuple(names)


def allowlist_tuple(value: object) -> tuple[frozenset[int], ...]:
    if not isinstance(value, list) and not isinstance(value, tuple):
        raise TypeError("Expected concept allowlists")

    allowlists: list[frozenset[int]] = []
    for item in value:
        if not isinstance(item, list) and not isinstance(item, tuple):
            raise TypeError("Expected a list of topic ids")

        topic_ids: set[int] = set()
        for topic_id in item:
            if not isinstance(topic_id, int) or isinstance(topic_id, bool):
                raise TypeError("Expected integer topic ids")

            topic_ids.add(topic_id)

        allowlists.append(frozenset(topic_ids))

    return tuple(allowlists)


def scheme_to_payload(scheme: LabelScheme) -> dict[str, object]:
    allowed: list[list[int]] = []
    for topic_ids in scheme.concept_allowed_topic_ids:
        allowed.append(sorted(topic_ids))

    return {
        "roles": list(scheme.roles),
        "topics": list(scheme.topics),
        "concepts": list(scheme.concepts),
        "concept_allowed_topic_ids": allowed,
    }


def scheme_from_dict(payload: dict[str, object]) -> LabelScheme:
    roles = string_tuple(payload["roles"])
    topics = string_tuple(payload["topics"])
    concepts = string_tuple(payload["concepts"])
    allowlists = allowlist_tuple(payload["concept_allowed_topic_ids"])
    legacy_ids: list[int] = []
    for allowed in allowlists:
        if allowed:
            legacy_ids.append(next(iter(allowed)))
        else:
            legacy_ids.append(-1)

    return apply_official_concept_topics(
        LabelScheme(
            roles=roles,
            topics=topics,
            concepts=concepts,
            role_ids=identity_ids(roles),
            topic_ids=identity_ids(topics),
            concept_ids=identity_ids(concepts),
            concept_topic_ids=tuple(legacy_ids),
            concept_allowed_topic_ids=allowlists,
        )
    )


def scheme_from_payload(raw: object) -> LabelScheme:
    if isinstance(raw, LabelScheme):
        return apply_official_concept_topics(raw)

    if isinstance(raw, dict):
        return scheme_from_dict(raw)

    raise TypeError("Checkpoint scheme must be a dict of label names")
