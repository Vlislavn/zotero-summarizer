"""Service layer for zotero-summarizer.

Modules are grouped by domain:

- ``model/``   — the relevance gate ML: classifier (+persistence), llm_classifier,
  tune, active_learning, label_weights, golden_metrics, library_features, the
  scoring blend (scoring/prestige/surprise), and ``eval_baseline/``.
- ``golden/``  — labels & ground truth: goldenset, label_provenance, hybrid_gt,
  feedback, and the ``relabel_audit/`` reliability study.
- ``triage/``  — the RSS daemon pipeline: feeds, summarization, select,
  triage_jobs, triage_backlog, daily_actions, and the ``daily_select/`` slate.
- ``library/`` — Stage-2 reading + feed review: reading_queue, deep_review,
  quality_review, border_cache, review, review_detail.
- ``zotero/``  — write path: zotero (read helpers), pending changes, note_analyzer.

Shared/infra stay at the top level: ``_common``, ``_adapters``, ``lifecycle``,
``run_log``, ``config``, ``health``, ``results``, ``corpus``, ``emoji_signals``.

This package is intentionally a thin namespace. Eagerly re-exporting classes
from `pending`/`summarization`/`triage_jobs` here creates a circular import
because those modules pull in `api.errors`, which pulls in `api.app`, which
pulls back mid-initialization. Consumers should import directly from the
submodule (e.g., ``from zotero_summarizer.services.zotero.pending import
PendingChangePlanner``).
"""
from __future__ import annotations

__all__: list[str] = []
