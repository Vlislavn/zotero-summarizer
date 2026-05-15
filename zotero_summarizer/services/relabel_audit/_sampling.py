"""Eligibility predicate + stratified sampling for the relabel-audit pool.

The boundary predicate :func:`is_eligible_row` is the ONLY function in
this module that classifies golden-CSV rows as eligible/not; downstream
construction trusts the predicate and fails-fast on mismatch (Hooker et
al. 2019 — stratification across age × class avoids decay-window
confounding).
"""
from __future__ import annotations

import csv
import logging
import random
from pathlib import Path

from zotero_summarizer.services.relabel_audit._constants import (
    AGE_BUCKET_EDGES,
    AGE_BUCKET_NAMES,
    AUDIT_PRIORITY_NAMES,
    DEFAULT_SAMPLE_SIZE,
    MIN_PER_CLASS,
    SAMPLING_SEED,
    AuditCandidate,
)

LOGGER = logging.getLogger(__name__)


def _age_bucket(days: int) -> str:
    """Return the bucket name. ``days`` must satisfy days >= AGE_BUCKET_EDGES[0]."""
    if days < AGE_BUCKET_EDGES[0]:
        raise ValueError(
            f"_age_bucket called with days={days} below the minimum "
            f"{AGE_BUCKET_EDGES[0]}; caller must filter via is_eligible_row first"
        )
    for upper, name in zip(AGE_BUCKET_EDGES[1:], AGE_BUCKET_NAMES[:-1]):
        if days < upper:
            return name
    return AGE_BUCKET_NAMES[-1]


def is_eligible_row(row: dict[str, str]) -> bool:
    """Boundary predicate: should this golden-CSV row enter the audit pool?

    Filters out rows missing required fields, with un-parseable numerics,
    or too recent (still inside the 90-day decay-active window). Downstream
    construction trusts this predicate and fails-fast on mismatch.
    """
    days_str = (row.get("days_since_added") or "").strip()
    if not days_str:
        return False
    if not (days_str.lstrip("-").isdigit()):
        return False
    days = int(days_str)
    if days < AGE_BUCKET_EDGES[0]:
        return False
    if not (row.get("title") or "").strip():
        return False
    if not (row.get("abstract") or "").strip():
        return False
    if (row.get("gold_priority_final") or "").strip() not in AUDIT_PRIORITY_NAMES:
        return False
    rel_str = (row.get("gold_inferred_relevance") or "").strip()
    if not rel_str:
        return False
    try:
        float(rel_str)
    except ValueError:
        # Boundary-level validation: malformed numeric -> not eligible. The
        # caller's job is to skip this row, not raise — the predicate's
        # whole contract is "True/False eligibility, no exceptions".
        return False
    return True


def _build_candidate(row: dict[str, str]) -> AuditCandidate:
    """Build an AuditCandidate from a row already proven eligible.

    Caller MUST have filtered via :func:`is_eligible_row` first; any
    inconsistency is a programming error and we raise.
    """
    if not is_eligible_row(row):
        raise ValueError(
            f"_build_candidate called on row {row.get('item_key')!r} "
            f"that fails is_eligible_row — caller must filter first"
        )
    days = int(row["days_since_added"].strip())
    return AuditCandidate(
        item_key=(row.get("item_key") or "").strip(),
        title=row["title"].strip(),
        authors=(row.get("authors") or "").strip(),
        venue=(row.get("venue") or "").strip(),
        abstract=row["abstract"].strip(),
        days_since_added=days,
        age_bucket=_age_bucket(days),
        original_priority=row["gold_priority_final"].strip(),
        original_inferred_relevance=float(row["gold_inferred_relevance"].strip()),
    )


def sample_stratified(
    rows: list[dict[str, str]],
    *,
    sample_size: int = DEFAULT_SAMPLE_SIZE,
    seed: int = SAMPLING_SEED,
) -> list[AuditCandidate]:
    """Stratified sample across (age_bucket × priority_class)."""
    rng = random.Random(seed)
    pool = [_build_candidate(row) for row in rows if is_eligible_row(row)]
    if not pool:
        raise ValueError(
            "no candidates passed filtering — golden CSV missing days_since_added?"
        )

    per_bucket_target = sample_size // len(AGE_BUCKET_NAMES)
    chosen: list[AuditCandidate] = []
    for bucket in AGE_BUCKET_NAMES:
        bucket_pool = [c for c in pool if c.age_bucket == bucket]
        if not bucket_pool:
            LOGGER.warning("audit sampling: bucket %s is empty", bucket)
            continue
        by_class: dict[str, list[AuditCandidate]] = {p: [] for p in AUDIT_PRIORITY_NAMES}
        for c in bucket_pool:
            by_class[c.original_priority].append(c)
        bucket_picks: list[AuditCandidate] = []
        for cls in AUDIT_PRIORITY_NAMES:
            n_cls = min(MIN_PER_CLASS, len(by_class[cls]))
            if n_cls > 0:
                bucket_picks.extend(rng.sample(by_class[cls], n_cls))
        already = {c.item_key for c in bucket_picks}
        remaining = [c for c in bucket_pool if c.item_key not in already]
        need = per_bucket_target - len(bucket_picks)
        if need > 0 and remaining:
            bucket_picks.extend(rng.sample(remaining, min(need, len(remaining))))
        chosen.extend(bucket_picks)

    rng.shuffle(chosen)
    return chosen[:sample_size]


def load_golden_rows(csv_path: Path) -> list[dict[str, str]]:
    if not csv_path.exists():
        raise FileNotFoundError(f"golden CSV not found at {csv_path}")
    with csv_path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


__all__ = ["is_eligible_row", "sample_stratified", "load_golden_rows"]
