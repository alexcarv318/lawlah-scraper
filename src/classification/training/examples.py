import re
from collections import defaultdict
from pathlib import Path

import torch
from datasets import Dataset, DatasetDict  # type: ignore[import-untyped]
from torch import Tensor, tensor
from torch.utils.data import DataLoader, WeightedRandomSampler
from transformers import AutoTokenizer, DataCollatorWithPadding
from transformers.tokenization_utils_base import PreTrainedTokenizerBase

from src.classification.training.labels import LabelScheme, concept_input, role_input, scheme_from_payload
from src.classification.training.metrics import apply_thresholds, maximum_labels
from src.classification.training.model import TextClassifier, checkpoint_weights, read_checkpoint

PARAGRAPH_INDEX = re.compile(r"#\[(\d+)\]")
PROVISION_INDEX = re.compile(r"#pr(\d+)")


def string_names(values: object) -> list[str]:
    names: list[str] = []
    if not isinstance(values, list):
        return names

    for value in values:
        if isinstance(value, str):
            names.append(value)

    return names


def resolve_device() -> torch.device:
    if torch.cuda.is_available():
        return torch.device("cuda")

    if torch.backends.mps.is_available():
        return torch.device("mps")

    return torch.device("cpu")


def load_tokenizer(encoder_name: str) -> PreTrainedTokenizerBase:
    return AutoTokenizer.from_pretrained(encoder_name)


def document_order(row_id: str) -> int:
    paragraph = PARAGRAPH_INDEX.search(row_id)
    if paragraph:
        return int(paragraph.group(1))

    provision = PROVISION_INDEX.search(row_id)
    if provision:
        return int(provision.group(1))

    return 0


def previous_paragraphs(row_ids: list[str], document_ids: list[str], texts: list[str]) -> list[str]:
    grouped: dict[str, list[int]] = defaultdict(list)
    for index, document_id in enumerate(document_ids):
        grouped[document_id].append(index)

    previous = [""] * len(texts)
    for indexes in grouped.values():
        indexes.sort(key=lambda index: document_order(row_ids[index]))
        for position, index in enumerate(indexes):
            if position == 0:
                continue

            previous[index] = texts[indexes[position - 1]]

    return previous


def add_previous_text(splits: DatasetDict) -> DatasetDict:
    updated = DatasetDict()
    for name, split in splits.items():
        previous = previous_paragraphs(
            row_ids=list(split["id"]),
            document_ids=list(split["document_id"]),
            texts=list(split["text"]),
        )
        updated[name] = split.add_column("previous_text", previous)

    return updated


def prepare_task(
    splits: DatasetDict,
    scheme: LabelScheme,
    task: str,
    encoder_name: str,
    max_length: int,
) -> tuple[DatasetDict, PreTrainedTokenizerBase]:
    tokenizer = load_tokenizer(encoder_name)

    def prepare_batch(batch: dict[str, list[object]]) -> dict[str, object]:
        texts: list[str] = []
        raw_texts = batch["text"]
        topic_rows = batch["topics"]
        previous_rows = batch.get("previous_text")
        prefix_rows = batch.get("concept_topics")

        for index, text in enumerate(raw_texts):
            raw = text if isinstance(text, str) else ""
            if task == "role":
                previous = ""
                if isinstance(previous_rows, list) and index < len(previous_rows):
                    previous_value = previous_rows[index]
                    previous = previous_value if isinstance(previous_value, str) else ""

                texts.append(role_input(text=raw, previous_text=previous))
                continue

            if task == "concepts":
                source_topics = topic_rows[index]
                if isinstance(prefix_rows, list) and index < len(prefix_rows):
                    source_topics = prefix_rows[index]

                texts.append(concept_input(text=raw, topics=string_names(source_topics)))
                continue

            texts.append(raw)

        tokens = tokenizer(texts, truncation=True, max_length=max_length)
        prepared: dict[str, object] = {
            "input_ids": tokens["input_ids"],
            "attention_mask": tokens["attention_mask"],
        }

        if task == "role":
            role_ids: list[int] = []
            for role in batch["role"]:
                role_name = role if isinstance(role, str) else None
                role_ids.append(scheme.role_id(role_name))

            prepared["role_id"] = role_ids
            return prepared

        if task == "topics":
            topic_targets: list[list[float]] = []
            for topics in topic_rows:
                topic_targets.append(scheme.topic_targets(string_names(topics)))

            prepared["label_targets"] = topic_targets
            return prepared

        concept_targets: list[list[float]] = []
        concept_masks: list[list[float]] = []
        for topics, concepts in zip(topic_rows, batch["concepts"], strict=True):
            topic_row = scheme.topic_targets(string_names(topics))
            concept_targets.append(scheme.concept_targets(string_names(concepts)))
            concept_masks.append(scheme.concept_mask(topic_row))

        prepared["label_targets"] = concept_targets
        prepared["label_mask"] = concept_masks
        return prepared

    columns = ["input_ids", "attention_mask"]
    if task == "role":
        columns.append("role_id")
    elif task == "topics":
        columns.append("label_targets")
    else:
        columns.append("label_targets")
        columns.append("label_mask")

    prepared_splits = DatasetDict()
    for name, split in splits.items():
        encoded = split.map(function=prepare_batch, batched=True, remove_columns=split.column_names)
        encoded.set_format(type="torch", columns=columns)
        prepared_splits[name] = encoded

    return prepared_splits, tokenizer


def predict_topic_names(
    texts: list[str],
    checkpoint_path: Path,
    scheme: LabelScheme,
    device: torch.device,
    batch_size: int,
) -> list[list[str]]:
    payload = read_checkpoint(checkpoint_path, device)
    encoder_name = payload["encoder_name"]
    decode_scheme = scheme_from_payload(payload["scheme"])
    raw_length = payload.get("max_length", 512)
    max_length = raw_length if isinstance(raw_length, int) and not isinstance(raw_length, bool) else 512
    raw_thresholds = payload.get("thresholds", [0.5] * len(decode_scheme.topics))
    if not isinstance(encoder_name, str):
        raise TypeError("Topics checkpoint is missing encoder_name")

    thresholds: list[float] = []
    if isinstance(raw_thresholds, list):
        for value in raw_thresholds:
            thresholds.append(float(value))

    if len(thresholds) != len(decode_scheme.topics):
        thresholds = [0.5] * len(decode_scheme.topics)

    model = TextClassifier(encoder_name=encoder_name, label_count=len(decode_scheme.topics))
    model.load_state_dict(checkpoint_weights(payload))
    model.to(device)
    model.float()
    model.eval()
    tokenizer = load_tokenizer(encoder_name)
    names: list[list[str]] = []

    for start in range(0, len(texts), batch_size):
        batch = texts[start : start + batch_size]
        tokens = tokenizer(batch, truncation=True, max_length=max_length, padding=True, return_tensors="pt")
        input_ids = tokens["input_ids"]
        attention_mask = tokens["attention_mask"]
        if not isinstance(input_ids, Tensor) or not isinstance(attention_mask, Tensor):
            raise TypeError("Tokenizer must return tensors")

        with torch.no_grad():
            logits = model(input_ids=input_ids.to(device), attention_mask=attention_mask.to(device))

        if not isinstance(logits, Tensor):
            raise TypeError("Model must return a tensor")

        flags = apply_thresholds(
            scores=logits.sigmoid().cpu(),
            thresholds=thresholds,
            maximum=maximum_labels("topics"),
        )
        for row in flags:
            names.append(list(decode_scheme.decode_topics(row.tolist())))

    return names


def add_concept_topics(splits: DatasetDict, predicted: dict[str, list[list[str]]]) -> DatasetDict:
    updated = DatasetDict()
    for name, split in splits.items():
        prefixes: list[list[str]] = []
        predicted_rows = predicted[name]
        for index, gold in enumerate(split["topics"]):
            gold_names = string_names(gold)
            if name != "train":
                prefixes.append(predicted_rows[index])
                continue

            if index % 2 == 0:
                prefixes.append(gold_names)
            else:
                prefixes.append(predicted_rows[index])

        updated[name] = split.add_column("concept_topics", prefixes)

    return updated


def role_class_weights(split: Dataset, role_count: int) -> Tensor:
    counts = [0] * role_count
    for role_id in split["role_id"]:
        index = int(role_id)
        if index >= 0:
            counts[index] += 1

    total = sum(counts)
    weights: list[float] = []
    for count in counts:
        if count == 0:
            weights.append(0.0)
        else:
            weights.append(total / (role_count * count))

    return tensor(weights)


def multilabel_sample_weights(split: Dataset) -> Tensor:
    rows = split["label_targets"]
    label_count = len(rows[0])
    counts = [0.0] * label_count

    for row in rows:
        for index, flag in enumerate(row):
            if float(flag) > 0.0:
                counts[index] += 1.0

    weights: list[float] = []
    for row in rows:
        weight = 0.0
        for index, flag in enumerate(row):
            if float(flag) > 0.0 and counts[index] > 0.0:
                weight += 1.0 / counts[index]

        weights.append(max(weight, 1e-3))

    return tensor(weights)


def make_loader(
    split: Dataset,
    tokenizer: PreTrainedTokenizerBase,
    batch_size: int,
    shuffle: bool,
    sample_weights: Tensor | None = None,
    pin_memory: bool = False,
) -> DataLoader[dict[str, Tensor]]:
    sampler = None
    if sample_weights is not None:
        weight_values: list[float] = []
        for value in sample_weights.tolist():
            weight_values.append(float(value))

        sampler = WeightedRandomSampler(
            weights=weight_values,
            num_samples=len(weight_values),
            replacement=True,
        )
        shuffle = False

    return DataLoader(
        dataset=split,
        batch_size=batch_size,
        shuffle=shuffle,
        sampler=sampler,
        collate_fn=DataCollatorWithPadding(tokenizer),
        pin_memory=pin_memory,
    )
