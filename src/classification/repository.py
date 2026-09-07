from pathlib import Path
from typing import Any

import httpx
import torch
from torch import Tensor
from transformers import AutoTokenizer
from transformers.tokenization_utils_base import PreTrainedTokenizerBase

from src.classification.schema import Classification
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


class TrainedHead:
    def __init__(
        self,
        model: TextClassifier,
        tokenizer: PreTrainedTokenizerBase,
        scheme: LabelScheme,
        max_length: int,
        thresholds: list[float],
        device: torch.device,
        task: str,
    ) -> None:
        self.model = model
        self.tokenizer = tokenizer
        self.scheme = scheme
        self.max_length = max_length
        self.thresholds = thresholds
        self.device = device
        self.task = task


def resolve_device() -> torch.device:
    if torch.cuda.is_available():
        return torch.device("cuda")
    if torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def load_tokenizer(encoder_name: str) -> PreTrainedTokenizerBase:
    return AutoTokenizer.from_pretrained(encoder_name)


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


class LocalClassifier:
    def __init__(self, role: TrainedHead, topics: TrainedHead, concepts: TrainedHead) -> None:
        self.role = role
        self.topics = topics
        self.concepts = concepts
        self.scheme = topics.scheme
        self.device = role.device

    @classmethod
    def load(cls, source: str) -> "LocalClassifier":
        device = resolve_device()
        logger.info("Loading %s classifiers on %s", source, device)
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


class RemoteClassifier:
    def __init__(
        self,
        source: str,
        base_url: str,
        instance_id: str | None,
        region: str,
    ) -> None:
        self.source = source
        self.base_url = base_url.rstrip("/")
        self.instance_id = instance_id
        self.region = region
        self.http = httpx.Client(timeout=httpx.Timeout(180.0, connect=10.0))

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

        self.ensure_running()
        response = self.http.post(
            f"{self.base_url}/classify",
            json={
                "source": self.source,
                "texts": texts,
                "previous_texts": previous_texts,
            },
        )
        response.raise_for_status()
        payload = response.json()
        items = payload.get("items")
        if not isinstance(items, list):
            raise TypeError("Classifier API must return items")

        results: list[Classification] = []
        for item in items:
            if not isinstance(item, dict):
                raise TypeError("Classifier API item must be an object")
            role = item.get("role")
            topics = item.get("topics")
            concepts = item.get("concepts")
            if not isinstance(role, str) or not isinstance(topics, list) or not isinstance(concepts, list):
                raise TypeError("Classifier API item is missing role, topics, or concepts")
            results.append(
                Classification(
                    role=role,
                    topics=tuple(str(name) for name in topics),
                    concepts=tuple(str(name) for name in concepts),
                )
            )
        if len(results) != len(texts):
            raise ValueError("Classifier API returned a different number of results than texts")
        return results

    def ensure_running(self) -> None:
        if self.healthy():
            return
        if not self.instance_id:
            raise RuntimeError(f"Classifier at {self.base_url} is down and no instance id is configured")

        logger.info("Starting classifier instance %s", self.instance_id)
        client = self.ec2_client()
        client.start_instances(InstanceIds=[self.instance_id])
        waiter = client.get_waiter("instance_running")
        waiter.wait(InstanceIds=[self.instance_id], WaiterConfig={"Delay": 15, "MaxAttempts": 40})

        for attempt in range(80):
            if self.healthy():
                logger.info("Classifier is ready after %s health checks", attempt + 1)
                return
            logger.info("Waiting for classifier health (%s/80)", attempt + 1)
            self.sleep(15)

        raise RuntimeError(f"Classifier instance {self.instance_id} started but /health never succeeded")

    def healthy(self) -> bool:
        try:
            response = self.http.get(f"{self.base_url}/health", timeout=5.0)
        except httpx.HTTPError:
            return False
        if response.status_code != 200:
            return False
        payload = response.json()
        return isinstance(payload, dict) and payload.get("ok") is True and payload.get("ready") is True

    def ec2_client(self) -> Any:
        import boto3

        return boto3.client("ec2", region_name=self.region)

    @staticmethod
    def sleep(seconds: float) -> None:
        from time import sleep

        sleep(seconds)
