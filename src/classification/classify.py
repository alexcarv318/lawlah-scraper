import argparse
from dataclasses import dataclass
from pathlib import Path

import torch
from torch import Tensor
from transformers.tokenization_utils_base import PreTrainedTokenizerBase

from src.classification.training.examples import load_tokenizer, resolve_device
from src.classification.training.labels import (
    CHECKPOINT_ROOT,
    TASKS,
    LabelScheme,
    concept_input,
    role_input,
    scheme_from_payload,
)
from src.classification.training.metrics import apply_thresholds, decode_thresholds, maximum_labels
from src.classification.training.model import TextClassifier, checkpoint_weights, read_checkpoint
from src.logger import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class Classification:
    role: str
    topics: tuple[str, ...]
    concepts: tuple[str, ...]


@dataclass
class TrainedHead:
    model: TextClassifier
    tokenizer: PreTrainedTokenizerBase
    scheme: LabelScheme
    max_length: int
    thresholds: list[float]
    device: torch.device
    task: str


def load_head(path: Path, device: torch.device) -> TrainedHead:
    if not path.exists():
        raise FileNotFoundError(f"No trained model at {path}. Train first.")

    payload = read_checkpoint(path, device)
    encoder_name = payload["encoder_name"]
    task = payload["task"]
    raw_length = payload.get("max_length", 512)
    max_length = raw_length if isinstance(raw_length, int) and not isinstance(raw_length, bool) else 512
    if not isinstance(encoder_name, str) or not isinstance(task, str):
        raise TypeError("Checkpoint is missing encoder_name or task")

    scheme = scheme_from_payload(payload["scheme"])
    if task == "role":
        count = len(scheme.roles)
    elif task == "topics":
        count = len(scheme.topics)
    else:
        count = len(scheme.concepts)

    model = TextClassifier(encoder_name=encoder_name, label_count=count)
    model.load_state_dict(checkpoint_weights(payload))
    model.to(device)
    if device.type == "mps":
        model.float()

    model.eval()

    raw_thresholds = payload.get("thresholds", [0.5] * count)
    thresholds: list[float] = []
    if isinstance(raw_thresholds, list):
        for value in raw_thresholds:
            thresholds.append(float(value))

    if len(thresholds) != count:
        thresholds = [0.5] * count

    logger.info("Loaded %s from %s", task, path)
    return TrainedHead(
        model=model,
        tokenizer=load_tokenizer(encoder_name),
        scheme=scheme,
        max_length=max_length,
        thresholds=decode_thresholds(thresholds),
        device=device,
        task=task,
    )


def predict_logits(head: TrainedHead, texts: list[str]) -> Tensor:
    if not texts:
        raise ValueError("texts must not be empty")

    tokens = head.tokenizer(
        texts,
        truncation=True,
        padding=True,
        max_length=head.max_length,
        return_tensors="pt",
    )
    input_ids = tokens["input_ids"]
    attention_mask = tokens["attention_mask"]
    if not isinstance(input_ids, Tensor) or not isinstance(attention_mask, Tensor):
        raise TypeError("Tokenizer must return tensors")

    input_ids = input_ids.to(head.device)
    attention_mask = attention_mask.to(head.device)

    with torch.inference_mode():
        logits = head.model(input_ids=input_ids, attention_mask=attention_mask)

    if not isinstance(logits, Tensor):
        raise TypeError("Model must return a tensor")

    return logits


class Classifier:
    def __init__(self, role: TrainedHead, topics: TrainedHead, concepts: TrainedHead) -> None:
        self.role = role
        self.topics = topics
        self.concepts = concepts
        self.scheme = topics.scheme

    @classmethod
    def load(cls, source: str) -> "Classifier":
        device = resolve_device()
        heads: dict[str, TrainedHead] = {}
        for task in TASKS:
            heads[task] = load_head(CHECKPOINT_ROOT / source / task / "best.pt", device)

        logger.info("Loaded %s role, topic, and concept classifiers", source)
        return cls(role=heads["role"], topics=heads["topics"], concepts=heads["concepts"])

    def classify(self, text: str, previous_text: str = "") -> Classification:
        return self.classify_many([text], [previous_text])[0]

    def classify_many(
        self,
        texts: list[str],
        previous_texts: list[str] | None = None,
    ) -> list[Classification]:
        if not texts:
            return []
        if previous_texts is None:
            previous_texts = [""] * len(texts)
        if len(previous_texts) != len(texts):
            raise ValueError("previous_texts must match texts")

        role_logits = predict_logits(
            self.role,
            [role_input(text=text, previous_text=previous) for text, previous in zip(texts, previous_texts, strict=True)],
        )
        topic_flags = apply_thresholds(
            scores=predict_logits(self.topics, texts).sigmoid(),
            thresholds=self.topics.thresholds,
            maximum=maximum_labels("topics"),
        )
        topic_flag_rows = topic_flags.tolist()
        topics_by_row = [self.scheme.decode_topics(row) for row in topic_flag_rows]

        concept_scores = predict_logits(
            self.concepts,
            [
                concept_input(text=text, topics=list(topics))
                for text, topics in zip(texts, topics_by_row, strict=True)
            ],
        ).sigmoid()
        allowed = torch.tensor(
            [self.scheme.concept_mask(row) for row in topic_flag_rows],
            device=concept_scores.device,
        )
        concept_flags = apply_thresholds(
            scores=concept_scores,
            thresholds=self.concepts.thresholds,
            maximum=maximum_labels("concepts"),
        ) * allowed

        results: list[Classification] = []
        for role_row, topics, concept_row in zip(
            role_logits,
            topics_by_row,
            concept_flags.tolist(),
            strict=True,
        ):
            results.append(
                Classification(
                    role=self.scheme.roles[int(role_row.argmax())],
                    topics=topics,
                    concepts=self.scheme.decode_concepts(concept_row),
                )
            )
        return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Classify one text with trained role, topic, and concept models.")
    parser.add_argument("--source", choices=["judgments", "legislation"], required=True)
    parser.add_argument("--text", required=True)
    parser.add_argument("--previous", default="")
    arguments = parser.parse_args()

    result = Classifier.load(arguments.source).classify(arguments.text, previous_text=arguments.previous)
    print(f"role: {result.role}")
    print(f"topics: {', '.join(result.topics) or '—'}")
    print(f"concepts: {', '.join(result.concepts) or '—'}")


if __name__ == "__main__":
    main()
