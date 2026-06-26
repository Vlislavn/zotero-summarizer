"""review_fleet — background pre-decision of the Read-next top-N reading verdicts.

A heavier companion to ``reading_queue``: it pre-computes a ``ProposedVerdict``
(must/should/could/dont_read + confidence + rationale + flags) for each top pick so
the human only Confirms or Overrides instead of deciding from scratch. The judgement
signals (read/skim/skip, A-D grade, quality band, overstatements, goal match) ALREADY
exist in the cached deep review — the fleet reads them, it does NOT re-run the LLM.

Layout (each module ≤500 LOC, single responsibility):
    propose.py       pure deterministic signals -> ProposedVerdict (no LLM, no I/O)
    verdict_store.py atomic JSON sidecar (data/<model_dir>/proposed_verdicts.json)
    fleet.py         single-flight serial background job + status()
    prewarm.py       launch-time schedule_on_startup (sibling of deep_review_prewarm)

The fleet writes ONLY the proposal sidecar — never label_verdicts, never Zotero.
``reading_queue`` reads the sidecar and attaches ``proposed_verdict`` to each row,
but a ``dont_read`` SUGGESTION is never routed through the hide/pin verdict logic.
"""
from __future__ import annotations

from zotero_summarizer.services.library.review_fleet.fleet import start, status
from zotero_summarizer.services.library.review_fleet.prewarm import schedule_on_startup

__all__ = ["start", "status", "schedule_on_startup"]
