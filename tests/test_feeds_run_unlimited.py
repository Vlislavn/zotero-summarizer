"""Phase 1.8: feeds run --feeds N exhausts ALL unread (regression test).

The previous bug at services/feeds.py:421 converted ``batch_size=None`` from
the CLI into the daemon default (5). Fixed: ``None`` means "unlimited"
everywhere; the daemon loop passes ``daemon_batch_size`` explicitly.

The pick/triage phases now live in :mod:`_tick_phases`; ``run_daemon_tick`` (in
:mod:`_tick`) orchestrates them. The picker + provider seams are patched on the
phases module; the config/reader/writer seams stay on the orchestrator.
"""

from __future__ import annotations

from unittest.mock import patch

from zotero_summarizer.services.triage.feeds import _tick as feeds
from zotero_summarizer.services.triage.feeds import _tick_phases as phases


def test_daemon_tick_with_batch_size_none_calls_pick_with_none(monkeypatch):
    """run_daemon_tick(batch_size=None) must NOT silently fall back to 5."""
    seen: list[int | None] = []

    def fake_pick(reader, *, batch_size, feed_library_ids, exclude_feed_names=None):
        seen.append(batch_size)
        return []  # empty -> nothing else to do

    # run_triage_stage resolves the feed-stage provider (for concurrency sizing);
    # this unit test doesn't bootstrap app_state, so stub that one call.
    monkeypatch.setattr(phases.get_state(), "resolve_stage_provider", lambda stage: None)
    with patch.object(phases, "_pick_unread_batch_round_robin", side_effect=fake_pick), \
         patch.object(feeds, "_load_config", return_value={"feeds": {"daemon_batch_size": 5}, "selection": {}, "surprise": {}}), \
         patch.object(feeds, "ZoteroReader"), patch.object(feeds, "ZoteroWriter"):
        feeds.run_daemon_tick(batch_size=None, dry_run=True)

    assert seen == [None], (
        f"Expected batch_size=None to be passed through unchanged; got {seen}. "
        "If you see 5 here, the daemon-default fallback regressed."
    )


def test_daemon_tick_with_explicit_int_passes_int(monkeypatch):
    """Bounded mode (daemon loop) passes an explicit integer."""
    seen: list[int | None] = []

    def fake_pick(reader, *, batch_size, feed_library_ids, exclude_feed_names=None):
        seen.append(batch_size)
        return []

    monkeypatch.setattr(phases.get_state(), "resolve_stage_provider", lambda stage: None)
    with patch.object(phases, "_pick_unread_batch_round_robin", side_effect=fake_pick), \
         patch.object(feeds, "_load_config", return_value={"feeds": {"daemon_batch_size": 5}, "selection": {}, "surprise": {}}), \
         patch.object(feeds, "ZoteroReader"), patch.object(feeds, "ZoteroWriter"):
        feeds.run_daemon_tick(batch_size=7, dry_run=True)

    assert seen == [7]
