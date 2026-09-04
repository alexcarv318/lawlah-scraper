import argparse
from pathlib import Path

import torch
from datasets import Dataset  # type: ignore[import-untyped]
from torch import Tensor
from torch.optim import AdamW
from torch.optim.lr_scheduler import LambdaLR
from torch.utils.data import DataLoader
from transformers import get_linear_schedule_with_warmup

from src.classification.training.examples import (
    add_concept_topics,
    add_previous_text,
    make_loader,
    multilabel_sample_weights,
    predict_topic_names,
    prepare_task,
    resolve_device,
    role_class_weights,
)
from src.classification.training.labels import (
    CHECKPOINT_ROOT,
    DEFAULT_ENCODER,
    DEFAULT_EPOCHS,
    ROLE_ENCODER,
    TASKS,
    LabelScheme,
    build_label_scheme,
    load_splits,
    scheme_for_source,
    scheme_from_payload,
    scheme_to_payload,
)
from src.classification.training.metrics import (
    apply_thresholds,
    decode_thresholds,
    maximum_labels,
    multilabel_scores,
    role_scores,
    tune_thresholds,
)
from src.classification.training.model import TextClassifier, checkpoint_weights, read_checkpoint
from src.logger import get_logger

logger = get_logger(__name__)


def label_count(scheme: LabelScheme, task: str) -> int:
    if task == "role":
        return len(scheme.roles)

    if task == "topics":
        return len(scheme.topics)

    return len(scheme.concepts)


def asymmetric_loss(
    logits: Tensor,
    targets: Tensor,
    mask: Tensor | None = None,
    gamma_negative: float = 4.0,
    gamma_positive: float = 0.0,
    clip: float = 0.05,
) -> Tensor:
    probabilities = logits.sigmoid()
    positive_probabilities = probabilities.clamp(min=1e-8, max=1.0)
    negative_probabilities = (probabilities - clip).clamp(min=0.0, max=1.0)

    positive_term = targets * torch.log(positive_probabilities) * (1.0 - probabilities) ** gamma_positive
    negative_term = (1.0 - targets) * torch.log((1.0 - negative_probabilities).clamp(min=1e-8))
    negative_term = negative_term * (negative_probabilities**gamma_negative)
    per_label = -(positive_term + negative_term)

    if mask is None:
        return per_label.mean()

    return (per_label * mask).sum() / mask.sum().clamp(min=1.0)


def run_epoch(
    model: TextClassifier,
    loader: DataLoader[dict[str, Tensor]],
    task: str,
    optimizer: AdamW | None,
    scheduler: LambdaLR | None,
    current_device: torch.device,
    role_weights: Tensor | None,
    thresholds: list[float] | None,
) -> tuple[dict[str, float], list[float] | None]:
    training = optimizer is not None
    model.train(training)

    role_ids: list[int] = []
    role_predictions: list[int] = []
    label_targets: list[Tensor] = []
    label_scores: list[Tensor] = []
    total_loss = 0.0
    steps = 0

    for batch in loader:
        moved: dict[str, Tensor] = {}
        for key, value in batch.items():
            if isinstance(value, Tensor):
                moved[key] = value.to(current_device)

        if optimizer is not None:
            optimizer.zero_grad()

        logits = model(input_ids=moved["input_ids"], attention_mask=moved["attention_mask"])

        if task == "role":
            loss = torch.nn.functional.cross_entropy(
                input=logits,
                target=moved["role_id"],
                weight=role_weights,
                ignore_index=-100,
            )
        else:
            mask = moved.get("label_mask")
            loss = asymmetric_loss(logits=logits, targets=moved["label_targets"], mask=mask)

        if optimizer is not None:
            loss.backward()  # type: ignore[no-untyped-call]
            torch.nn.utils.clip_grad_norm_(parameters=model.parameters(), max_norm=1.0)
            optimizer.step()
            if scheduler is not None:
                scheduler.step()

        total_loss += float(loss.detach())
        steps += 1

        if training and steps % 200 == 0:
            logger.info("%s batch %s loss %.4f", task, steps, float(loss.detach()))

        if task == "role":
            role_ids.extend(moved["role_id"].tolist())
            role_predictions.extend(logits.argmax(dim=-1).tolist())
            continue

        label_targets.append(moved["label_targets"].detach().float().cpu())
        label_scores.append(logits.sigmoid().detach().float().cpu())

    scores: dict[str, float] = {"loss": total_loss / max(steps, 1)}

    if task == "role":
        scores.update(role_scores(role_ids=role_ids, role_predictions=role_predictions))
        return scores, None

    targets = torch.cat(label_targets)
    probabilities = torch.cat(label_scores)
    maximum = maximum_labels(task)

    if thresholds is None:
        thresholds = tune_thresholds(targets=targets, scores=probabilities)
    else:
        thresholds = decode_thresholds(thresholds)

    predictions = apply_thresholds(scores=probabilities, thresholds=thresholds, maximum=maximum)
    scores.update(multilabel_scores(targets=targets, predictions=predictions))
    return scores, thresholds


def load_checkpoint(path: Path, device: torch.device) -> dict[str, object]:
    if not path.exists():
        raise FileNotFoundError(f"No checkpoint at {path}")

    return read_checkpoint(path, device)


def checkpoint_integer(payload: dict[str, object], key: str, default: int) -> int:
    value = payload.get(key, default)
    if isinstance(value, bool):
        return default

    if isinstance(value, int):
        return value

    if isinstance(value, float):
        return int(value)

    return default


def prepare_source(
    source: str,
    task: str,
    encoder_name: str,
    max_length: int,
    batch_size: int,
    current_device: torch.device,
) -> tuple[
    LabelScheme,
    Dataset,
    DataLoader[dict[str, Tensor]],
    DataLoader[dict[str, Tensor]],
    DataLoader[dict[str, Tensor]],
]:
    raw = load_splits(source)
    scheme = build_label_scheme(raw)
    if task == "role":
        raw = add_previous_text(raw)

    if task == "concepts":
        topics_path = CHECKPOINT_ROOT / source / "topics" / "best.pt"
        predicted: dict[str, list[list[str]]] = {}
        for split_name, split in raw.items():
            texts: list[str] = []
            for text in split["text"]:
                texts.append(text if isinstance(text, str) else "")

            logger.info("Predicting topics for %s/%s (%s rows)", source, split_name, len(texts))
            predicted[split_name] = predict_topic_names(
                texts=texts,
                checkpoint_path=topics_path,
                scheme=scheme,
                device=current_device,
                batch_size=batch_size,
            )

        raw = add_concept_topics(raw, predicted)

    encoded, tokenizer = prepare_task(
        splits=raw,
        scheme=scheme,
        task=task,
        encoder_name=encoder_name,
        max_length=max_length,
    )
    sample_weights = None
    if task in {"topics", "concepts"}:
        sample_weights = multilabel_sample_weights(encoded["train"])

    pin_memory = current_device.type == "cuda"
    train_loader = make_loader(
        split=encoded["train"],
        tokenizer=tokenizer,
        batch_size=batch_size,
        shuffle=sample_weights is None,
        sample_weights=sample_weights,
        pin_memory=pin_memory,
    )
    validation_loader = make_loader(
        split=encoded["validation"],
        tokenizer=tokenizer,
        batch_size=batch_size,
        shuffle=False,
        pin_memory=pin_memory,
    )
    test_loader = make_loader(
        split=encoded["test"],
        tokenizer=tokenizer,
        batch_size=batch_size,
        shuffle=False,
        pin_memory=pin_memory,
    )
    return scheme, encoded["train"], train_loader, validation_loader, test_loader


def decode_task(source: str, task: str, checkpoint: Path, batch_size: int) -> None:
    current_device = resolve_device()
    payload = load_checkpoint(checkpoint, current_device)
    encoder_name = payload["encoder_name"]
    max_length = checkpoint_integer(payload, "max_length", 512)
    if not isinstance(encoder_name, str):
        raise TypeError("Checkpoint is missing encoder_name")

    scheme = scheme_for_source(scheme_from_payload(payload["scheme"]), source)
    _, _, _, validation_loader, test_loader = prepare_source(
        source=source,
        task=task,
        encoder_name=encoder_name,
        max_length=max_length,
        batch_size=batch_size,
        current_device=current_device,
    )
    model = TextClassifier(encoder_name=encoder_name, label_count=label_count(scheme, task))
    model.load_state_dict(checkpoint_weights(payload))
    model.to(current_device)
    model.float()

    validation_scores, thresholds = run_epoch(
        model=model,
        loader=validation_loader,
        task=task,
        optimizer=None,
        scheduler=None,
        current_device=current_device,
        role_weights=None,
        thresholds=None,
    )
    if thresholds is None:
        raise TypeError("Decode-only needs a multilabel task")

    payload["thresholds"] = thresholds
    payload["scheme"] = scheme_to_payload(scheme)
    torch.save(payload, checkpoint)
    test_scores, _ = run_epoch(
        model=model,
        loader=test_loader,
        task=task,
        optimizer=None,
        scheduler=None,
        current_device=current_device,
        role_weights=None,
        thresholds=thresholds,
    )
    logger.info("decode-only %s/%s validation %s", source, task, validation_scores)
    logger.info("decode-only %s/%s test %s", source, task, test_scores)
    logger.info("Wrote thresholds to %s", checkpoint)


def train_task(
    source: str,
    task: str,
    encoder_name: str,
    epochs: int,
    batch_size: int,
    learning_rate: float,
    max_length: int,
    checkpoint: Path | None,
) -> None:
    current_device = resolve_device()
    scheme, train_split, train_loader, validation_loader, test_loader = prepare_source(
        source=source,
        task=task,
        encoder_name=encoder_name,
        max_length=max_length,
        batch_size=batch_size,
        current_device=current_device,
    )
    model = TextClassifier(encoder_name=encoder_name, label_count=label_count(scheme, task))
    if checkpoint is not None:
        saved = load_checkpoint(checkpoint, current_device)
        saved_encoder = saved["encoder_name"]
        if saved_encoder != encoder_name:
            raise ValueError(f"Checkpoint encoder {saved_encoder} does not match {encoder_name}")

        model.load_state_dict(checkpoint_weights(saved))
        logger.info("Resumed %s/%s from %s", source, task, checkpoint)

    model.to(current_device)
    model.float()

    role_weights = None
    if task == "role":
        role_weights = role_class_weights(train_split, len(scheme.roles))
        role_weights = role_weights.to(device=current_device, dtype=torch.float32)

    optimizer = AdamW(params=model.parameters(), lr=learning_rate, weight_decay=0.01)
    total_steps = epochs * len(train_loader)
    scheduler = get_linear_schedule_with_warmup(
        optimizer=optimizer,
        num_warmup_steps=int(total_steps * 0.06),
        num_training_steps=total_steps,
    )  # type: ignore[no-untyped-call]

    output_directory = CHECKPOINT_ROOT / source / task
    output_directory.mkdir(parents=True, exist_ok=True)
    best_score = -1.0
    best_thresholds: list[float] = [0.5] * label_count(scheme, task)

    logger.info(
        "Training %s/%s on %s (%s labels, %s train batches)",
        source,
        task,
        current_device,
        label_count(scheme, task),
        len(train_loader),
    )

    for epoch in range(1, epochs + 1):
        train_scores, _ = run_epoch(
            model=model,
            loader=train_loader,
            task=task,
            optimizer=optimizer,
            scheduler=scheduler,
            current_device=current_device,
            role_weights=role_weights,
            thresholds=best_thresholds,
        )
        validation_scores, epoch_thresholds = run_epoch(
            model=model,
            loader=validation_loader,
            task=task,
            optimizer=None,
            scheduler=None,
            current_device=current_device,
            role_weights=role_weights,
            thresholds=None,
        )
        if epoch_thresholds is None:
            epoch_thresholds = best_thresholds

        logger.info("epoch %s %s/%s train %s", epoch, source, task, train_scores)
        logger.info("epoch %s %s/%s validation %s", epoch, source, task, validation_scores)

        payload = {
            "model": model.state_dict(),
            "scheme": scheme_to_payload(scheme),
            "encoder_name": encoder_name,
            "max_length": max_length,
            "task": task,
            "thresholds": epoch_thresholds,
        }
        torch.save(payload, output_directory / f"epoch-{epoch}.pt")

        score = float(validation_scores["macro_f1"])
        if score > best_score:
            best_score = score
            best_thresholds = epoch_thresholds
            torch.save(payload, output_directory / "best.pt")
            logger.info("Saved %s", output_directory / "best.pt")

    best_path = output_directory / "best.pt"
    saved = load_checkpoint(best_path, current_device)
    model.load_state_dict(checkpoint_weights(saved))
    saved_thresholds = saved.get("thresholds", best_thresholds)
    if isinstance(saved_thresholds, list):
        best_thresholds = decode_thresholds([float(value) for value in saved_thresholds])

    test_scores, _ = run_epoch(
        model=model,
        loader=test_loader,
        task=task,
        optimizer=None,
        scheduler=None,
        current_device=current_device,
        role_weights=role_weights,
        thresholds=None if task == "role" else best_thresholds,
    )
    logger.info("test %s/%s %s", source, task, test_scores)


def train(
    source: str,
    tasks: tuple[str, ...],
    encoder_name: str,
    epochs: int | None,
    batch_size: int,
    learning_rate: float,
    max_length: int,
    checkpoint: Path | None,
    decode_only: bool,
) -> None:
    for task in tasks:
        task_checkpoint = checkpoint
        if task_checkpoint is None:
            task_checkpoint = CHECKPOINT_ROOT / source / task / "best.pt"

        if decode_only:
            decode_task(source=source, task=task, checkpoint=task_checkpoint, batch_size=batch_size)
            continue

        resume = checkpoint
        task_epochs = epochs if epochs is not None else DEFAULT_EPOCHS[task]
        task_encoder = encoder_name
        if len(tasks) == 1 and task == "role" and encoder_name == DEFAULT_ENCODER:
            task_encoder = ROLE_ENCODER

        train_task(
            source=source,
            task=task,
            encoder_name=task_encoder,
            epochs=task_epochs,
            batch_size=batch_size,
            learning_rate=learning_rate,
            max_length=max_length,
            checkpoint=resume,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Train separate role, topic, and concept classifiers.")
    parser.add_argument("--source", choices=["judgments", "legislation"], required=True)
    parser.add_argument("--task", choices=["all", *TASKS], default="all")
    parser.add_argument("--encoder", default=DEFAULT_ENCODER)
    parser.add_argument("--epochs", type=int, default=None)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--learning-rate", type=float, default=None)
    parser.add_argument("--max-length", type=int, default=512)
    parser.add_argument("--checkpoint", default=None)
    parser.add_argument("--decode-only", action="store_true")
    arguments = parser.parse_args()

    selected = TASKS if arguments.task == "all" else (arguments.task,)
    checkpoint = Path(arguments.checkpoint) if arguments.checkpoint else None
    learning_rate = arguments.learning_rate
    if learning_rate is None:
        learning_rate = 5e-6 if checkpoint is not None else 2e-5

    train(
        source=arguments.source,
        tasks=selected,
        encoder_name=arguments.encoder,
        epochs=arguments.epochs,
        batch_size=arguments.batch_size,
        learning_rate=learning_rate,
        max_length=arguments.max_length,
        checkpoint=checkpoint,
        decode_only=arguments.decode_only,
    )


if __name__ == "__main__":
    main()
