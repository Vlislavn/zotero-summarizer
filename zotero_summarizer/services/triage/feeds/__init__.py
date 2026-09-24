"""RSS daemon: score feed papers and review the Today slate in place.

No daemon tick adds a feed paper to Zotero or auto-rejects an undecided pick.
The explicit reviewed Add path owns materialization; manual `select-daily` is
an advisory read-only preview. Implementations live in the `_daily`, `_tick`,
`_gate`, `_triage`, `_outcomes` and `_loop` modules.
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
    install_gate,
    schedule_gate_retrain_async,
    schedule_slate_rescore_async,
)
from zotero_summarizer.services.triage.feeds._daily import (  # noqa: F401
    _refine_with_full_text,
    run_daily_selection,
)
from zotero_summarizer.services.triage.feeds._daily_materialize import (  # noqa: F401
    _feed_payload_from_row,
    _matched_collections_from_row,
    _summary_from_row,
    _tags_from_row,
    materialize_pick,
)
from zotero_summarizer.services.triage.feeds._outcomes import (  # noqa: F401
    _compute_outcome_from_membership,
    _resolve_due_outcomes,
)
from zotero_summarizer.services.triage.feeds._tick import run_daemon_tick  # noqa: F401
from zotero_summarizer.services.triage.feeds._tick_phases import (  # noqa: F401
    _pick_unread_batch_round_robin,
)
from zotero_summarizer.services.triage.feeds._loop import run_daemon_loop  # noqa: F401
