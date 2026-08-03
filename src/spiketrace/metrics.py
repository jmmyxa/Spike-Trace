from __future__ import annotations

from collections.abc import Sequence


def classification_metrics(
    targets: Sequence[int], predictions: Sequence[int], labels: Sequence[str]
) -> dict[str, object]:
    if len(targets) != len(predictions):
        raise ValueError("targets and predictions must have the same length.")
    if not labels:
        raise ValueError("labels cannot be empty.")

    size = len(labels)
    confusion = [[0 for _ in range(size)] for _ in range(size)]
    for target, prediction in zip(targets, predictions):
        if not 0 <= target < size or not 0 <= prediction < size:
            raise ValueError("Class index is outside the label range.")
        confusion[target][prediction] += 1

    per_class: dict[str, dict[str, float | int]] = {}
    f1_values: list[float] = []
    correct = 0
    for index, label in enumerate(labels):
        true_positive = confusion[index][index]
        false_positive = (
            sum(confusion[row][index] for row in range(size)) - true_positive
        )
        false_negative = sum(confusion[index]) - true_positive
        support = sum(confusion[index])
        precision = (
            true_positive / (true_positive + false_positive)
            if true_positive + false_positive
            else 0.0
        )
        recall = true_positive / (true_positive + false_negative) if support else 0.0
        f1 = (
            2 * precision * recall / (precision + recall) if precision + recall else 0.0
        )
        per_class[label] = {
            "precision": round(precision, 6),
            "recall": round(recall, 6),
            "f1": round(f1, 6),
            "support": support,
        }
        f1_values.append(f1)
        correct += true_positive

    total = len(targets)
    return {
        "accuracy": round(correct / total, 6) if total else 0.0,
        "macro_f1": round(sum(f1_values) / len(f1_values), 6),
        "per_class": per_class,
        "confusion_matrix": {
            "labels": list(labels),
            "values": confusion,
        },
        "samples": total,
    }
