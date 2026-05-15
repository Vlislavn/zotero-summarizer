from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from zotero_summarizer.services import emoji_signals


def _parse_created_at(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        if value.endswith("Z"):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        return datetime.fromisoformat(value)
    except Exception:
        return None


def infer_feedback_events_from_corpus_items(
    items: list[Any],
    stale_days_for_weak_negative: int,
    latest_results_by_item_id: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    latest_results_by_item_id = latest_results_by_item_id or {}
    events: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc)

    def append_event(item_id: str, base_feedback_type: str, signal: str, inferred_relevance: float) -> None:
        latest_result = latest_results_by_item_id.get(item_id) or {}
        original_priority = str(
            latest_result.get("forced_priority") or latest_result.get("reading_priority") or ""
        ).strip()
        predicted_score_raw = latest_result.get("composite_score")
        if predicted_score_raw is None:
            predicted_score_raw = latest_result.get("relevance_score")

        predicted_score: float | None = None
        if predicted_score_raw is not None:
            try:
                predicted_score = float(predicted_score_raw)
            except (TypeError, ValueError):
                predicted_score = None

        feedback_type = base_feedback_type
        if predicted_score is not None:
            if inferred_relevance >= 4.0 and predicted_score <= 2.5:
                feedback_type = f"{base_feedback_type}_false_negative"
            elif inferred_relevance <= 2.0 and predicted_score >= 3.5:
                feedback_type = f"{base_feedback_type}_false_positive"

        events.append(
            {
                "item_id": item_id,
                "feedback_type": feedback_type,
                "signal": signal,
                "original_priority": original_priority,
                "inferred_relevance": inferred_relevance,
            }
        )

    for item in items:
        tags = [str(t) for t in (getattr(item, "tags", []) or []) if t]
        annotation_count = int(getattr(item, "annotation_count", 0) or 0)
        manual_note_count = int(getattr(item, "manual_note_count", 0) or 0)
        created_at = _parse_created_at(getattr(item, "created_at", None))

        item_id = str(getattr(item, "item_id", "")).strip()
        if not item_id:
            continue

        # Walk the shared emoji taxonomy: any matched emoji fires its tier's event.
        signals = emoji_signals.detect_signals(tags)
        has_emoji_negative = False
        has_emoji_positive = False
        for sig in signals:
            base = (
                "implicit_negative"
                if sig.tier in ("strong_negative", "boring")
                else "implicit_engagement"
            )
            # Translate the additive score_delta back into an absolute
            # 1..5 inferred_relevance value for the user_feedback row.
            sig_relevance = max(1.0, min(5.0, emoji_signals.NEUTRAL_SCORE + sig.score_delta))
            append_event(item_id, base, _signal_label(sig), sig_relevance)
            if base == "implicit_negative":
                has_emoji_negative = True
            else:
                has_emoji_positive = True

        if annotation_count > 0:
            append_event(item_id, "implicit_engagement", "has_annotations", 4.5)
        if manual_note_count > 0:
            append_event(item_id, "implicit_engagement", "manual_note", 4.0)

        has_positive = has_emoji_positive or annotation_count > 0 or manual_note_count > 0
        if not has_positive and not has_emoji_negative and created_at is not None:
            age_days = (now - created_at.astimezone(timezone.utc)).days
            if age_days >= stale_days_for_weak_negative:
                append_event(item_id, "implicit_weak_negative", "stale_without_engagement", 2.0)

    return events


def _signal_label(sig: "emoji_signals.EmojiSignal") -> str:
    """Stable identifier used as the ``signal`` column in `user_feedback`."""
    return f"{sig.tier}:{sig.emoji}"
