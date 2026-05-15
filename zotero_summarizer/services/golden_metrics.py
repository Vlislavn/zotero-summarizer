"""Classification metrics for golden-set evaluation.

Pure functions over parallel ``gold`` / ``pred`` label lists. No I/O, no
state — callers are responsible for loading rows and persisting reports.

The four expected class labels match
:class:`zotero_summarizer.domain.ReadingPriority`:
``must_read``, ``should_read``, ``could_read``, ``dont_read``.
"""

from __future__ import annotations

from dataclasses import dataclass


CLASSES: tuple[str, ...] = ("must_read", "should_read", "could_read", "dont_read")
POSITIVE_CLASSES: frozenset[str] = frozenset({"must_read", "should_read"})


@dataclass(frozen=True)
class ClassMetrics:
    precision: float
    recall: float
    f1: float
    support: int               # number of gold instances of this class
    true_positive: int
    false_positive: int
    false_negative: int

    def as_dict(self) -> dict[str, float | int]:
        return {
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
            "support": self.support,
            "true_positive": self.true_positive,
            "false_positive": self.false_positive,
            "false_negative": self.false_negative,
        }


def _safe_div(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator > 0 else 0.0


def _metrics_from_counts(tp: int, fp: int, fn: int, support: int) -> ClassMetrics:
    precision = _safe_div(tp, tp + fp)
    recall = _safe_div(tp, tp + fn)
    f1 = _safe_div(2 * precision * recall, precision + recall)
    return ClassMetrics(
        precision=precision,
        recall=recall,
        f1=f1,
        support=support,
        true_positive=tp,
        false_positive=fp,
        false_negative=fn,
    )


def compute_per_class(
    gold: list[str],
    pred: list[str],
    classes: tuple[str, ...] = CLASSES,
) -> dict[str, ClassMetrics]:
    """Per-class precision / recall / F1, one-vs-rest.

    Length mismatch raises ``ValueError`` — caller guarantees parallel lists.
    """
    if len(gold) != len(pred):
        raise ValueError(f"gold/pred length mismatch: {len(gold)} vs {len(pred)}")
    out: dict[str, ClassMetrics] = {}
    for cls in classes:
        tp = sum(1 for g, p in zip(gold, pred) if g == cls and p == cls)
        fp = sum(1 for g, p in zip(gold, pred) if g != cls and p == cls)
        fn = sum(1 for g, p in zip(gold, pred) if g == cls and p != cls)
        support = sum(1 for g in gold if g == cls)
        out[cls] = _metrics_from_counts(tp, fp, fn, support)
    return out


def compute_binary(
    gold: list[str],
    pred: list[str],
    positive: frozenset[str] = POSITIVE_CLASSES,
) -> ClassMetrics:
    """Binary keep/skip metrics: positive = items the user would read.

    Treats ``must_read`` ∪ ``should_read`` as positive (1) and everything else
    as negative (0).
    """
    if len(gold) != len(pred):
        raise ValueError(f"gold/pred length mismatch: {len(gold)} vs {len(pred)}")
    tp = sum(1 for g, p in zip(gold, pred) if g in positive and p in positive)
    fp = sum(1 for g, p in zip(gold, pred) if g not in positive and p in positive)
    fn = sum(1 for g, p in zip(gold, pred) if g in positive and p not in positive)
    support = sum(1 for g in gold if g in positive)
    return _metrics_from_counts(tp, fp, fn, support)


def compute_confusion(
    gold: list[str],
    pred: list[str],
    classes: tuple[str, ...] = CLASSES,
) -> list[list[int]]:
    """Confusion matrix: ``matrix[gold_idx][pred_idx]`` = count.

    Rows are gold labels (in ``classes`` order), columns are predicted labels.
    The diagonal is correct predictions.
    """
    if len(gold) != len(pred):
        raise ValueError(f"gold/pred length mismatch: {len(gold)} vs {len(pred)}")
    idx = {c: i for i, c in enumerate(classes)}
    n = len(classes)
    matrix = [[0] * n for _ in range(n)]
    for g, p in zip(gold, pred):
        if g not in idx or p not in idx:
            continue   # silently drop unknown labels (e.g. empty pred)
        matrix[idx[g]][idx[p]] += 1
    return matrix


def accuracy(gold: list[str], pred: list[str]) -> float:
    """Overall accuracy = correctly-classified / total."""
    if len(gold) != len(pred):
        raise ValueError(f"gold/pred length mismatch: {len(gold)} vs {len(pred)}")
    if not gold:
        return 0.0
    return _safe_div(sum(1 for g, p in zip(gold, pred) if g == p), len(gold))
