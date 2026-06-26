"""Honest calibration of the review fleet's PROPOSED verdicts against the user's
CONFIRMED labels — both already stored, so this measures REAL human agreement with
no new data collection.

``compute_proposal_calibration`` matches every item that has BOTH a fleet proposal
(``verdict_store``) and a user label (``label_verdicts``), then reports agreement %
plus Cohen's kappa (chance-corrected). Until enough matched pairs accumulate the
result is flagged ``insufficient`` — the UI must present pipeline agreement as
SELF-CONSISTENCY across runs, never as human-validated accuracy. This is the
calibration scaffold the deep-review redesign promised: a path from "transparent,
evidence-linked checklist" to a measured human-agreement number as the user labels.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

# Below this many matched pairs, kappa is too noisy to present as validation (honest
# floor — Landis-Koch bands are meaningless on a handful of samples).
_MIN_PAIRS = 20


def cohen_kappa(a: list[str], b: list[str]) -> float | None:
    """Cohen's kappa for two equal-length nominal label sequences. ``None`` when
    undefined: no data, mismatched lengths, or a single constant category (chance
    agreement == 1, so kappa is degenerate)."""
    n = len(a)
    if n == 0 or n != len(b):
        return None
    labels = sorted(set(a) | set(b))
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum((ca[lab] / n) * (cb[lab] / n) for lab in labels)
    if pe >= 1.0:
        return None
    return round((po - pe) / (1.0 - pe), 3)


def compute_proposal_calibration(
    *, proposals: dict[str, Any] | None = None, labels: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Agreement between fleet proposals and the user's confirmed labels over the
    items that have BOTH. Reads the live stores when the args are omitted. Honest:
    flags ``insufficient`` (and frames the number as self-consistency) below
    ``_MIN_PAIRS`` matched pairs."""
    if proposals is None:
        from zotero_summarizer.services.library.review_fleet import verdict_store
        proposals = verdict_store.read_all()
    if labels is None:
        from zotero_summarizer.services.library import reading_queue
        labels = reading_queue._verdict_priorities()

    pairs = [
        (str((proposals[k] or {}).get("proposed") or ""), labels[k])
        for k in proposals
        if k in labels and labels[k] and (proposals[k] or {}).get("proposed")
    ]
    n = len(pairs)
    pred = [p for p, _ in pairs]
    human = [h for _, h in pairs]
    agreement = round(sum(1 for p, h in pairs if p == h) / n, 3) if n else 0.0
    insufficient = n < _MIN_PAIRS
    return {
        "n_pairs": n,
        "agreement": agreement,
        "cohen_kappa": cohen_kappa(pred, human) if n else None,
        "insufficient": insufficient,
        "note": (
            "agreement = self-consistency across runs, not yet human-validated"
            if insufficient
            else "fleet-proposal vs your confirmed-label agreement (Cohen's kappa)"
        ),
    }


__all__ = ["cohen_kappa", "compute_proposal_calibration"]
