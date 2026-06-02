"""feeds: the Phase 1.13 classifier gate, counterfactual audit, + retrain.

Fast-rejects obvious non-matches before the LLM, kicks off background
retrains when the golden CSV drifts, and synthesises gate-only candidates
for the label-bootstrap review flow (Phase 1.14).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from zotero_summarizer.services.triage.feeds._common import (
    LOGGER,
    TriagedCandidate,
    get_settings,
    get_state,
)

from zotero_summarizer.domain import PRIORITY_TO_RELEVANCE as _PRIORITY_TO_RELEVANCE  # noqa: E402


def _synthesize_gate_only_candidate(item: dict[str, Any]) -> "TriagedCandidate":
    """Build a TriagedCandidate from the classifier gate's prediction alone.

    Used by ``gate_only`` mode (Phase 1.14): the user wants to bootstrap
    golden-CSV labels through the review UI without paying for LLM calls on
    every item. The synthesised summary carries a placeholder rationale that
    points the reviewer at the SHAP attribution panel.

    Raises if the gate did not stamp the item — that means we tried to
    gate-only-triage something that bypassed the gate (a bug, not a degraded
    case to swallow).
    """
    from zotero_summarizer.models import SummarizeResponse

    pred = item.get("_gate_prediction")
    if pred is None:
        raise RuntimeError(
            f"gate_only triage requires a gate prediction on item; "
            f"_gate_prediction missing for {item.get('item_id')!r}. "
            "Did the gate run before this call?"
        )
    priority = pred.predicted_priority
    if priority not in _PRIORITY_TO_RELEVANCE:
        raise ValueError(
            f"gate produced unknown priority {priority!r}; "
            f"expected one of {sorted(_PRIORITY_TO_RELEVANCE)}"
        )
    relevance = _PRIORITY_TO_RELEVANCE[priority]
    summary = SummarizeResponse(
        executive_summary=(
            "(gate-only prediction — no LLM rationale; "
            "see SHAP attribution + author/venue panel)"
        ),
        relevance_score=relevance,
        composite_relevance_score=float(pred.calibrated_score) * 5.0,
        reading_priority=priority,
        triage_rationale=(
            f"Predicted by classifier gate "
            f"(raw={pred.raw_score:.4f}, cal={pred.calibrated_score:.4f}). "
            "Open the Feed Review tab to inspect SHAP contributions."
        ),
        triage_confidence=float(pred.calibrated_score),
        corpus_affinity_score=0.0,
        suggested_collections=[],
        tags=["zs:gate-only"],
        prestige_score=None,
    )
    cand = TriagedCandidate(
        feed_item=item,
        summary=summary,
        composite_score=float(pred.calibrated_score) * 5.0,
        surprise_score=0.0,
    )
    return cand


def _pack_review_payload(item: dict[str, Any], summary: Any = None) -> str | None:
    """Serialise gate SHAP + aux_context + LLM summary for the review UI.

    Stored verbatim in ``processed_feed_items.shap_contribs_json``. Shape:

        {"shap": [{"feature": str, "contribution": float}, ...] | None,
         "aux_context": {"max_author_h_index": float, ...}    | None,
         "summary": {reading_priority, tags, rationale, ...}  | None}

    Returns None when nothing meaningful was computed (gate disabled AND no
    LLM summary). The review API parses this back when listing items.
    """
    import json as _json

    shap = item.get("_gate_shap_contribs")
    aux = item.get("_gate_aux_context")
    summary_dict: dict[str, Any] | None = None
    if summary is not None:
        dump = getattr(summary, "model_dump", None)
        if dump is None:
            raise TypeError(
                "summary must be a pydantic BaseModel with model_dump(); "
                f"got {type(summary).__name__}"
            )
        summary_dict = dump()
    audit = bool(item.get("_resurrected_for_audit"))
    if shap is None and aux is None and summary_dict is None and not audit:
        return None
    payload: dict[str, Any] = {"shap": shap, "aux_context": aux, "summary": summary_dict}
    if audit:
        # Phase 1.15 (2.3): counterfactual gate audit marker. The review UI
        # renders a 🎲 chip for these so the user knows the gate said
        # dont_read on this item; their verdict feeds the audit metric.
        payload["audit_pick"] = True
    return _json.dumps(payload)


def _apply_classifier_gate(
    tick_id: str,
    items: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[tuple[dict[str, Any], Any]]]:
    """Phase 1.13 hybrid gate. Returns (survivors, rejected_with_prediction).

    Predict failures propagate to the caller — a broken gate must visibly
    fail the tick so the user fixes it. When the gate is not configured
    (lifecycle didn't load it), the daemon runs unchanged: returns
    ``(items, [])``.
    """
    app_state = get_state()
    gate = getattr(app_state, "classifier_gate", None)
    if gate is None or not items:
        return items, []

    config = get_state().app_state.config
    gate_cfg = getattr(config, "classifier_gate", None)
    drop_set: set[str] = set(gate_cfg.drop_priorities) if gate_cfg is not None else {"dont_read"}
    if not drop_set:
        # User configured an empty drop list — gate effectively disabled.
        return items, []

    settings_ = get_settings()
    predictions = gate.predict(
        items,
        corpus_db_path=settings_.corpus_db_path,
        goals_config=config,
        return_shap=True,
    )
    # Sprint-1 (May 2026): no longer need the Phase 1.14 `raw_score_dont_read_below`
    # override here — the regression-based classifier emits priorities through
    # `domain.score_to_priority`, which is the single source of truth for the
    # score → 4-class mapping. The legacy `gate_cfg.raw_score_dont_read_below`
    # field is preserved on the config dataclass for forward-compat but no
    # longer applied. See `docs/model-roadmap.md` (F4 fix).

    by_key = {p.item_key: p for p in predictions}
    survivors: list[dict[str, Any]] = []
    rejected: list[tuple[dict[str, Any], Any]] = []
    for item in items:
        cache_key = str(item.get("item_key") or item.get("item_id") or "")
        pred = by_key.get(cache_key)
        if pred is None:
            # Featurisation skipped this item (missing title/abstract). Forward
            # to the LLM path — that pipeline has its own handling for this.
            survivors.append(item)
            continue
        # Attach attribution onto the item dict so the triage loop can persist
        # it alongside the final decision (Phase 1.14 review UI).
        item["_gate_score"] = pred.calibrated_score
        item["_gate_priority"] = pred.predicted_priority
        item["_gate_shap_contribs"] = pred.shap_contribs
        item["_gate_aux_context"] = pred.aux_context
        item["_gate_raw_score"] = pred.raw_score
        item["_gate_prediction"] = pred
        if pred.predicted_priority in drop_set:
            rejected.append((item, pred))
        else:
            survivors.append(item)

    # Phase 1.15 (2.3): counterfactual gate audit. Randomly resurrect up to
    # `audit_sample_per_tick` rows from the rejected pile and push them through
    # the pipeline as if the gate had let them through. User's verdict on these
    # is a clean unbiased estimate of gate false-negative rate.
    audit_n = int(gate_cfg.audit_sample_per_tick)
    resurrected: list[tuple[dict[str, Any], Any]] = []
    if audit_n > 0 and rejected:
        import random as _random

        rng = _random.Random(tick_id)   # repro: same tick_id → same audit pick
        k = min(audit_n, len(rejected))
        picked_idx = rng.sample(range(len(rejected)), k)
        # Iterate from the highest index so list.pop() doesn't shift the rest.
        for i in sorted(picked_idx, reverse=True):
            item, pred = rejected.pop(i)
            item["_resurrected_for_audit"] = True
            resurrected.append((item, pred))
            survivors.append(item)

    LOGGER.info(
        "[%s] gate: %d rejected, %d survived (model=%s, drop=%s, audit=%d)",
        tick_id, len(rejected), len(survivors), gate.classifier_name,
        sorted(drop_set), len(resurrected),
    )
    return survivors, rejected


def schedule_gate_retrain_async(reason: str, *, allow_initial: bool = False) -> bool:
    """Schedule a background gate (re)train when needed; never blocks.

    Returns True if a retrain thread was started. Used by:
      * the per-tick daemon check (``_maybe_schedule_gate_retrain``), and
      * lifecycle startup, so a sha drift after Refresh-labels retrains in
        the background instead of blocking the server's startup for minutes.

    A retrain is scheduled when the golden CSV sha differs from the loaded
    gate's sha. With ``allow_initial=True`` it also schedules when no gate
    is loaded yet (fresh install) so the gate comes online shortly without
    blocking startup; the swap target requires ``classifier_gate_lock`` to
    be set by the caller.
    """
    app_state = get_state()
    if getattr(app_state, "classifier_gate_training", False):
        return False

    settings_ = get_settings()
    golden_csv = settings_.golden_csv_path
    if not golden_csv.exists():
        return False

    gate = getattr(app_state, "classifier_gate", None)
    if gate is None and not allow_initial:
        return False

    if gate is not None:
        from zotero_summarizer.services import run_log
        current_sha = run_log.file_sha256(golden_csv, prefix_len=64)
        if current_sha == gate.golden_csv_sha256:
            return False  # already fresh
        classifier_name = gate.classifier_name
        LOGGER.info(
            "[%s] golden CSV changed (%s → %s); retraining classifier in background",
            reason, gate.golden_csv_sha256[:12], current_sha[:12],
        )
    else:
        classifier_name = app_state.app_state.config.classifier_gate.model_name
        LOGGER.info(
            "[%s] no classifier gate loaded; training in background (gate off until ready)",
            reason,
        )

    app_state.classifier_gate_training = True
    import threading

    threading.Thread(
        target=_gate_retrain_worker,
        args=(golden_csv, classifier_name),
        name=f"gate-retrain-{reason}",
        daemon=True,
    ).start()
    return True


def _maybe_schedule_gate_retrain(tick_id: str) -> None:
    """Per-tick check: if the golden CSV's sha changed, kick off a retrain
    in a background thread. Current tick keeps using the stale gate;
    subsequent ticks observe the swap.
    """
    schedule_gate_retrain_async(tick_id)


def _gate_quality_label(md: dict[str, Any]) -> str:
    """AUC for legacy classification models, Spearman for the Sprint-1
    regression objective; ``quality=n/a`` when neither is present. Shared by
    ``install_gate`` and lifecycle so startup + retrain log the same way."""
    if "oof_auc" in md:
        return f"AUC={md['oof_auc']:.3f}"
    if "oof_spearman" in md:
        return f"Spearman={md['oof_spearman']:.3f}"
    return "quality=n/a"


def _rescore_slate_after_swap(reason: str) -> dict[str, Any] | None:
    """Re-score the live Today slate with the currently-installed gate.

    A retrain/upgrade gives the daemon a new gate, but the rows ALREADY on
    Today keep the scores the OLD gate gave them — triage decisions are
    terminal and never re-scored. Re-scoring here means the user sees the new
    model's ranking immediately, without having to hit
    ``POST /api/daily/rescore-slate`` by hand.

    Best-effort: the gate is already live, so a rescore failure must never
    propagate (it would wrongly look like the install/retrain itself failed).
    Lazy import breaks the ``rescore_slate`` ↔ ``feeds`` module cycle.
    """
    try:
        from zotero_summarizer.services.triage import rescore_slate
        result = rescore_slate.rescore_slate()
        LOGGER.info(
            "[%s] post-swap slate rescore: rescored=%d skipped=%s",
            reason, int(result.get("rescored", 0)), result.get("skipped"),
        )
        return result
    except Exception:  # noqa: BLE001 — best-effort; the gate swap already succeeded
        LOGGER.exception("[%s] post-swap slate rescore failed (non-fatal)", reason)
        return None


def install_gate(new_gate: Any, *, reason: str, rescore: bool = True) -> dict[str, Any] | None:
    """Atomically make ``new_gate`` the live classifier gate, then (by default)
    re-score the current Today slate so it reflects the new model at once.

    Single source of truth for "a freshly-trained gate is ready": both the
    daemon's background retrain (``_gate_retrain_worker``) and the UI-triggered
    ``POST /api/admin/retrain`` install through here, so the in-memory gate and
    the Today slate never drift from the on-disk artifact. The swap takes
    ``classifier_gate_lock`` when lifecycle set one (gate enabled); otherwise it
    assigns directly. Returns the rescore result dict (or ``None`` when rescoring
    is skipped/failed) for the caller's status payload.
    """
    app_state = get_state()
    lock = getattr(app_state, "classifier_gate_lock", None)
    if lock is not None:
        with lock:
            app_state.classifier_gate = new_gate
    else:
        app_state.classifier_gate = new_gate
    md = new_gate.training_metadata
    LOGGER.info(
        "classifier gate installed (%s): %s (n_train=%d, %s, golden_sha=%s)",
        reason,
        new_gate.classifier_name,
        md.get("n_train", 0),
        _gate_quality_label(md),
        new_gate.golden_csv_sha256[:12],
    )
    return _rescore_slate_after_swap(reason) if rescore else None


def schedule_slate_rescore_async(reason: str) -> None:
    """Re-score the Today slate on a background daemon thread (never blocks).

    Used at startup when a cached gate loads with an unchanged golden sha: no
    retrain fires, so ``install_gate``'s rescore never runs, and the slate would
    otherwise keep stale per-row scores from whenever each row was triaged (e.g.
    a model trained offline by the CLI, then loaded on the next server start).
    """
    import threading

    threading.Thread(
        target=_rescore_slate_after_swap,
        args=(reason,),
        name=f"slate-rescore-{reason}",
        daemon=True,
    ).start()


def _gate_retrain_worker(golden_csv: Path, classifier_name: str) -> None:
    """Background thread: retrain, then atomic swap + slate rescore via
    ``install_gate``. Errors are logged then re-raised so the default thread
    excepthook surfaces them; the daemon keeps using the old gate in the
    meantime."""
    from zotero_summarizer.services.model import classifier_persistence
    try:
        app_state = get_state()
        settings_ = get_settings()
        config = app_state.app_state.config
        gate_cfg = config.classifier_gate
        new_gate = classifier_persistence.load_or_train(
            golden_csv,
            classifier_name=classifier_name,
            corpus_db_path=settings_.corpus_db_path,
            goals_config=config,
            output_dir=classifier_persistence.DEFAULT_MODEL_DIR,
            force_retrain=True,
            n_folds=gate_cfg.n_folds,
            pca_dim=gate_cfg.pca_dim,
        )
        # Swap the new gate in AND re-score the live slate so Today reflects the
        # retrain immediately (single source of truth: install_gate). The rescore
        # is best-effort and never raises, so the except below stays scoped to
        # genuine train/swap failures.
        install_gate(new_gate, reason="daemon-retrain")
    except Exception:
        LOGGER.exception("background gate retrain failed; keeping previous gate")
        raise
    finally:
        # Always clear the in-progress flag so the next tick can retry.
        get_state().classifier_gate_training = False
