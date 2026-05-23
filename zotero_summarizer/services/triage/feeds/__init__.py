"""RSS-feed batch processor — the background triage daemon.

    Every N minutes (`run_daemon_tick`):
      - pick K unread feed items (round-robin across feeds)
      - classifier gate fast-rejects, then LLM-triage the survivors
      - record as `triaged_pending`; mark them read in Zotero
      - opportunistically resolve M due outcomes -> user_feedback

    Once per day (when ticks notice the daily window elapsed, `run_daily_selection`):
      - gather `triaged_pending` rows from the rolling window
      - plateau-select top 1-2 (+ optional black-swan slot)
      - materialize selected items DIRECTLY into Zotero (Inbox + matched
        collections + tags + v3 note) — bypasses the pending-changes queue
        because feed-sourced creates are low-blast-radius
      - schedule outcome detection N days out

User goal: "1-2 good papers daily to read from my feeds (best)". The daily
selection's hard_min defaults to 1 and hard_max to 2.

This package is a facade; implementation lives in the private sub-modules:
    _common   constants, the tick report dataclass, low-level helpers
    _triage   abstract-only triage primitive + concurrent scoring + prestige
    _gate     classifier gate + counterfactual audit + background retrain
    _daily    daily plateau selection + full-text refine + row reconstruction
    _tick     one daemon tick (the orchestration)
    _outcomes outcome detection -> feedback weights
    _loop     the long-running asyncio loop
"""
from __future__ import annotations

from zotero_summarizer.services.triage.feeds._common import (  # noqa: F401
    LOGGER,
    DaemonTickReport,
    TriagedCandidate,
    _DEFAULT_BLACK_SWAN_TAG,
    _ZOTERO_KEY_ALPHABET,
    _dim_value,
    _generate_zotero_key,
    _infer_item_type,
    _is_fatal_llm_error,
    _load_config,
    _parse_year,
    _safe_dict,
    _since_iso,
    _triage_conn,
    _triage_result_from_summary,
    list_feed_groups,
    preview_feed,
)
from zotero_summarizer.services.triage.feeds._triage import (  # noqa: F401
    _apply_prestige,
    _score_survivors,
    _triage_one,
)
from zotero_summarizer.services.triage.feeds._gate import (  # noqa: F401
    _apply_classifier_gate,
    _gate_retrain_worker,
    _maybe_schedule_gate_retrain,
    _pack_review_payload,
    _synthesize_gate_only_candidate,
    schedule_gate_retrain_async,
)
from zotero_summarizer.services.triage.feeds._daily import (  # noqa: F401
    _feed_payload_from_row,
    _matched_collections_from_row,
    _refine_with_full_text,
    _should_run_daily_selection,
    _summary_from_row,
    _tags_from_row,
    run_daily_selection,
)
from zotero_summarizer.services.triage.feeds._outcomes import (  # noqa: F401
    _compute_outcome_from_membership,
    _feedback_type_from_outcome,
    _relevance_from_weight,
    _resolve_due_outcomes,
)
from zotero_summarizer.services.triage.feeds._tick import (  # noqa: F401
    _pick_unread_batch_round_robin,
    run_daemon_tick,
)
from zotero_summarizer.services.triage.feeds._loop import run_daemon_loop  # noqa: F401
