"""Append-only agentic interaction log — the immutable human-decision trajectory.

Every deliberate human reading decision (and the 7-day behavioural outcome that
closes it) is appended as ONE JSON line to ``data/interaction-events.jsonl``,
paired with the supplied model prediction and stamped with the code
(``git_commit``) and model (``gate_sha``) version. Unless explicitly supplied,
``gate_sha`` identifies the gate loaded at event time, not historical inference.

Why this exists: the live decision tables (``label_verdicts``,
``role_value_verdicts``, ``user_feedback``) are UPSERT / DELETE — re-rating or
retracting OVERWRITES the prior value, destroying the trajectory. This log keeps
the full append-only history for offline improvement: confusion matrices per
model version, re-rating / retraction trajectories, and a future training
distiller. It is an AUDIT/TRAJECTORY record, never the authoritative training
label (that stays in ``label_verdicts`` / ``hybrid_gt``).

Reuses the ``run_log`` NDJSON primitive. Write and provenance errors propagate.
The durable decision may already be committed; propagation does not roll it back
or make the log and verdict store a single transaction.

Writer model: single-worker uvicorn, so human-feedback appends serialize on the
event loop. The 7-day ``feed_outcome`` emitter runs on the in-process triage
daemon thread — a second producer; small JSONL lines under ``PIPE_BUF`` append
atomically via ``O_APPEND``, so concurrent small writes are safe.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from zotero_summarizer.domain import READING_PRIORITY_SORT_RANK
from zotero_summarizer.services import run_log
from zotero_summarizer.services._common import settings, state

SCHEMA_VERSION = 1


def _utc_now_iso() -> str:
    """Single shared UTC stamp so API-process and daemon-thread events merge."""
    return datetime.now(timezone.utc).isoformat()


def key_kind(item_key: str) -> str:
    """Namespace tag for the heterogeneous ``item_key`` space.

    One paper carries different keys across surfaces (``feed:<id>`` /
    ``processed:<id>`` / ``note:<id>`` / raw Zotero key / arXiv URL); the tag lets
    a cross-surface consumer join instead of silently dropping mismatched rows.
    """
    if item_key.startswith("feed:"):
        return "feed"
    if item_key.startswith("processed:"):
        return "processed"
    if item_key.startswith("note:"):
        return "note"
    if item_key.startswith("http") and "arxiv.org" in item_key:
        return "arxiv_url"
    return "zotero"


def _current_gate_sha() -> str:
    """Exact loaded artifact identity; empty only when no gate is loaded."""
    gate = state().classifier_gate
    return gate.model_sha256 if gate is not None else ""


def _emit(event_kind: str, fields: dict[str, Any]) -> None:
    """Stamp the common envelope and append; I/O errors propagate."""
    event = {
        "ts": _utc_now_iso(),
        "event": event_kind,
        "schema": SCHEMA_VERSION,
        "git_commit": run_log.short_git_commit(),
        **fields,
    }
    run_log.append_run(settings().interaction_log_path, event)


def log_human_feedback(
    *,
    item_key: str,
    item_key_kind: str,
    surface: str,
    model: dict[str, Any],
    human: dict[str, Any],
    source: str = "user",
    comment: str = "",
    gate_sha: str | None = None,
    stable_id: dict[str, Any] | None = None,
) -> None:
    """Append one human-decision event: the model prediction + the human choice.

    ``model`` carries the prediction the human reacted to (priority and, where
    the surface has them, ``composite_score`` / ``surprise_score`` /
    ``corpus_affinity``). ``human`` carries ``{"kind": ..., "value": ...}``.
    ``item_key_kind`` + ``stable_id`` let cross-surface events for one paper join
    despite the heterogeneous key namespace (``feed:`` / ``processed:`` / Zotero /
    arXiv-URL).
    """
    _emit(
        "human_feedback",
        {
            "item_key": item_key,
            "item_key_kind": item_key_kind,
            "stable_id": stable_id or {},
            "surface": surface,
            "source": source,
            "gate_sha": _current_gate_sha() if gate_sha is None else gate_sha,
            "model": model,
            "human": human,
            "comment": comment,
        },
    )


def _log_label_transition(
    *,
    item_key: str,
    previous_user_priority: str | None,
    new_user_priority: str | None,
    model_priority: str | None,
    surface: str,
    previous_source: str | None = None,
    source: str = "user",
    comment: str = "",
    history_known: bool = True,
) -> None:
    """Append an explicit user-label assignment, change, or retraction.

    ``None`` means no prior label was recorded locally; ``history_known=False``
    keeps a label first observed from another device from fabricating history.
    Model/provenance sentinels are normalized to ``None`` rather than conflated
    with either user label.
    """
    if previous_user_priority == new_user_priority:
        return
    if previous_user_priority is None:
        transition = "assigned"
    elif new_user_priority is None:
        transition = "retracted"
    else:
        transition = "changed"
    _emit(
        "label_transition",
        {
            "item_key": item_key,
            "item_key_kind": key_kind(item_key),
            "surface": surface,
            "source": source,
            "previous_source": previous_source,
            "gate_sha": _current_gate_sha(),
            "transition": transition,
            "previous_user_priority": previous_user_priority,
            "new_user_priority": new_user_priority,
            "model_priority": (
                model_priority if model_priority in READING_PRIORITY_SORT_RANK else None
            ),
            "history_known": history_known,
            "comment": comment,
        },
    )


def _feed_stable_id(row: dict[str, Any]) -> dict[str, Any]:
    """Cross-namespace ids from a ``processed_feed_items`` row (for joins)."""
    fid = int(row.get("feed_item_id") or 0)
    return {"feed_item_id": fid or None, "doi": row.get("doi") or None,
            "arxiv": row.get("arxiv_id") or None}


def log_feed_decision(
    *,
    row: dict[str, Any],
    item_key: str,
    surface: str,
    human: dict[str, Any],
    source: str = "user",
    comment: str = "",
    model_priority: str | None = None,
    gate_sha: str | None = None,
) -> None:
    """``log_human_feedback`` for a ``processed_feed_items`` row.

    Extracts the model block (the gate's derived priority + the at-decision
    composite/surprise/corpus_affinity scores already on the row) so the Today,
    daily-verdict and review surfaces don't each re-build it. ``model_priority``
    overrides the priority for callers that mutate ``row["reading_priority"]``
    before logging (``add_to_library``).
    """
    log_human_feedback(
        item_key=item_key,
        item_key_kind=key_kind(item_key),
        surface=surface,
        source=source,
        model={
            "priority": model_priority
            if model_priority is not None
            else ((row.get("reading_priority") or "").strip() or "unknown"),
            "composite_score": row.get("composite_score"),
            "surprise_score": row.get("surprise_score"),
            "corpus_affinity": row.get("corpus_affinity"),
        },
        human=human,
        comment=comment,
        gate_sha=gate_sha,
        stable_id=_feed_stable_id(row),
    )


def log_behavioural_outcome(
    *,
    item_key: str,
    item_key_kind: str,
    model: dict[str, Any],
    outcome: str,
    signal_weight: float | None = None,
    elapsed_days: float | None = None,
    stable_id: dict[str, Any] | None = None,
    gate_sha: str | None = None,
    surface: str = "feed_outcome",
) -> None:
    """Append the 7-day behavioural outcome that closes a feed item's trajectory.

    This is the "…7 days later trashed" half of the model-vs-human story — the
    daemon-resolved engaged/moved/trashed/deleted signal, joined to the at-triage
    verdict by ``item_key`` in the same replayable stream.
    """
    _emit(
        "outcome_resolved",
        {
            "item_key": item_key,
            "item_key_kind": item_key_kind,
            "stable_id": stable_id or {},
            "surface": surface,
            "source": "outcome",
            "gate_sha": _current_gate_sha() if gate_sha is None else gate_sha,
            "model": model,
            "human": {
                "kind": "outcome",
                "value": outcome,
                "signal_weight": signal_weight,
                "elapsed_days": elapsed_days,
            },
        },
    )
