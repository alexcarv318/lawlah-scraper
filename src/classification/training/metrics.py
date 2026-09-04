from sklearn.metrics import accuracy_score, f1_score  # type: ignore[import-untyped]
from torch import Tensor

MINIMUM_THRESHOLD = 0.45
THRESHOLD_CANDIDATES = (0.45, 0.50, 0.55, 0.60)
MAX_TOPICS = 3
MAX_CONCEPTS = 4


def role_scores(role_ids: list[int], role_predictions: list[int]) -> dict[str, float]:
    gold_roles: list[int] = []
    predicted_roles: list[int] = []
    for gold, predicted in zip(role_ids, role_predictions, strict=True):
        if gold == -100:
            continue

        gold_roles.append(gold)
        predicted_roles.append(predicted)

    if not gold_roles:
        return {"accuracy": 0.0, "macro_f1": 0.0}

    return {
        "accuracy": float(accuracy_score(y_true=gold_roles, y_pred=predicted_roles)),
        "macro_f1": float(f1_score(y_true=gold_roles, y_pred=predicted_roles, average="macro", zero_division=0.0)),
    }


def multilabel_scores(targets: Tensor, predictions: Tensor) -> dict[str, float]:
    gold = targets.numpy()
    predicted = predictions.numpy()

    return {
        "micro_f1": float(f1_score(y_true=gold, y_pred=predicted, average="micro", zero_division=0.0)),
        "macro_f1": float(f1_score(y_true=gold, y_pred=predicted, average="macro", zero_division=0.0)),
    }


def decode_thresholds(thresholds: list[float]) -> list[float]:
    bounded: list[float] = []
    for value in thresholds:
        bounded.append(max(float(value), MINIMUM_THRESHOLD))

    return bounded


def maximum_labels(task: str) -> int | None:
    if task == "topics":
        return MAX_TOPICS

    if task == "concepts":
        return MAX_CONCEPTS

    return None


def tune_thresholds(targets: Tensor, scores: Tensor) -> list[float]:
    gold = targets.numpy()
    probabilities = scores.numpy()
    thresholds: list[float] = []

    for column in range(gold.shape[1]):
        best_threshold = 0.50
        best_f1 = -1.0

        for candidate in THRESHOLD_CANDIDATES:
            predicted = (probabilities[:, column] >= candidate).astype("float64")
            score = float(f1_score(y_true=gold[:, column], y_pred=predicted, zero_division=0.0))
            if score > best_f1:
                best_f1 = score
                best_threshold = candidate

        thresholds.append(best_threshold)

    return decode_thresholds(thresholds)


def apply_thresholds(scores: Tensor, thresholds: list[float], maximum: int | None) -> Tensor:
    cutoff = scores.new_tensor(decode_thresholds(thresholds))
    chosen = scores >= cutoff
    result = chosen.float()

    if maximum is None:
        return result

    capped = result.clone()
    for row_index, (row_scores, row_chosen) in enumerate(zip(scores, chosen, strict=True)):
        if int(row_chosen.sum()) <= maximum:
            continue

        selected = row_scores.masked_fill(~row_chosen, -1.0)
        winners = selected.topk(maximum).indices
        capped[row_index] = 0.0
        capped[row_index, winners] = 1.0

    return capped
