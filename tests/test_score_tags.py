"""Relevance-band Zotero tags: the mutually-exclusive zs:rel/<band> builder and
the bulk sync over the scored library (one backup, doesn't touch priority/manual
tags)."""
from __future__ import annotations

from types import SimpleNamespace

from zotero_summarizer.services.library import score_tags
from zotero_summarizer.services.zotero.pending import build_rel_tag_change


# --- build_rel_tag_change ---------------------------------------------------

def test_rel_tag_change_adds_target_on_empty():
    out = build_rel_tag_change([], "must_read")
    assert out == {"add_tags": ["zs:rel/must_read"], "remove_tags": []}


def test_rel_tag_change_is_mutually_exclusive_within_rel_namespace():
    out = build_rel_tag_change(["zs:rel/could_read"], "must_read")
    assert out["add_tags"] == ["zs:rel/must_read"]
    assert out["remove_tags"] == ["zs:rel/could_read"]


def test_rel_tag_change_never_touches_priority_or_emoji_tags():
    # Manual priority + emoji feedback must survive a rel-tag sync (manual-wins).
    current = ["zs:must_read", "🧠", "👀", "zs:rel/could_read"]
    out = build_rel_tag_change(current, "should_read")
    assert out["add_tags"] == ["zs:rel/should_read"]
    assert out["remove_tags"] == ["zs:rel/could_read"]   # only the rel band is swapped
    assert "zs:must_read" not in out["remove_tags"]
    assert "🧠" not in out["remove_tags"] and "👀" not in out["remove_tags"]


def test_rel_tag_change_idempotent_when_already_correct():
    out = build_rel_tag_change(["zs:rel/must_read", "🧠"], "must_read")
    assert out == {"add_tags": [], "remove_tags": []}


# --- sync_rel_tags ----------------------------------------------------------

class _Writer:
    def __init__(self, running=False):
        self._running = running
        self.calls: list = []

    def is_connector_running(self):
        return self._running

    def apply_changes(self, changes, create_backup):
        self.calls.append((changes, create_backup))
        return {"applied_ids": list(range(len(changes))), "failed": [], "backup_path": "/tmp/zotero.bak"}


def _reader(items):
    # Both sync_rel_tags and sync_score_ranks now read the WHOLE library via
    # get_all_items (which paginates past the 500 clamp; annotations excluded).
    return SimpleNamespace(get_all_items=lambda **kw: {"items": items, "total": len(items)})


def _cache(*entries):
    """item_key → {relevance, prestige, prestige_known} (the read_score_cache shape)."""
    return {
        k: {"relevance": rel, "prestige": pr, "prestige_known": known}
        for k, rel, pr, known in entries
    }


def test_sync_rel_tags_batches_with_one_backup_and_counts_bands(monkeypatch):
    monkeypatch.setattr(score_tags.reading_queue, "read_score_cache",
                        lambda: _cache(("K1", 4.8, None, False), ("K2", 1.2, None, False)))
    items = [
        {"item_key": "K1", "tags": []},                       # 4.8 → must_read (add)
        {"item_key": "K2", "tags": ["zs:rel/must_read"]},     # 1.2 → dont_read (swap)
    ]
    writer = _Writer(running=False)
    monkeypatch.setattr(score_tags, "get_zotero_reader_or_raise", lambda: _reader(items))
    monkeypatch.setattr(score_tags, "get_zotero_writer_or_raise", lambda: writer)

    out = score_tags.sync_rel_tags()

    assert out["tagged"] == 2
    assert out["by_band"] == {"must_read": 1, "dont_read": 1}
    assert out["backup_path"] == "/tmp/zotero.bak"
    assert len(writer.calls) == 1                       # ONE batched apply
    changes, create_backup = writer.calls[0]
    assert create_backup is True                        # backup-first
    assert len(changes) == 2
    # K2's change swaps the rel band only.
    k2 = next(c for c in changes if c["item_key"] == "K2")
    assert k2["payload_json"]["add_tags"] == ["zs:rel/dont_read"]
    assert k2["payload_json"]["remove_tags"] == ["zs:rel/must_read"]


def test_sync_rel_tags_demotes_low_prestige_top_item(monkeypatch):
    # Quality floor = median of KNOWN prestige [0.1, 0.5, 0.9] = 0.5. The
    # high-relevance item with prestige 0.1 (< floor) is demoted must→should;
    # the 0.9 one is kept must_read. Unknown prestige is never demoted.
    monkeypatch.setattr(score_tags.reading_queue, "read_score_cache", lambda: _cache(
        ("HI", 4.8, 0.1, True),    # top relevance, KNOWN low prestige → demote
        ("REF", 4.8, 0.9, True),   # top relevance, high prestige → keep must_read
        ("LOW", 1.0, 0.5, True),   # anchors the median; dont_read regardless
    ))
    items = [
        {"item_key": "HI", "tags": []},
        {"item_key": "REF", "tags": []},
        {"item_key": "LOW", "tags": []},
    ]
    writer = _Writer(running=False)
    monkeypatch.setattr(score_tags, "get_zotero_reader_or_raise", lambda: _reader(items))
    monkeypatch.setattr(score_tags, "get_zotero_writer_or_raise", lambda: writer)

    score_tags.sync_rel_tags()
    changes, _ = writer.calls[0]
    by_key = {c["item_key"]: c["payload_json"]["add_tags"] for c in changes}
    assert by_key["HI"] == ["zs:rel/should_read"]   # demoted from must_read
    assert by_key["REF"] == ["zs:rel/must_read"]     # high prestige kept
    assert by_key["LOW"] == ["zs:rel/dont_read"]


def test_sync_rel_tags_requires_force_when_zotero_running(monkeypatch):
    monkeypatch.setattr(score_tags.reading_queue, "read_score_cache",
                        lambda: _cache(("K1", 4.8, None, False)))
    monkeypatch.setattr(score_tags, "get_zotero_reader_or_raise", lambda: _reader([{"item_key": "K1", "tags": []}]))
    monkeypatch.setattr(score_tags, "get_zotero_writer_or_raise", lambda: _Writer(running=True))
    out = score_tags.sync_rel_tags(force=False)
    assert out.get("requires_force") is True


def test_sync_rel_tags_noop_without_scores(monkeypatch):
    monkeypatch.setattr(score_tags.reading_queue, "read_score_cache", lambda: {})
    out = score_tags.sync_rel_tags()
    assert out["tagged"] == 0


# --- sync_score_ranks (whole-library rank into Zotero Call Number) -----------

def test_sync_score_ranks_numbers_every_item_scored_first(monkeypatch):
    # Whole-library rank: scorable papers (in the global cache) rank on top by the
    # blend; a no-abstract paper (absent from the cache → relevance None) sinks to
    # the bottom but is STILL numbered. Every item gets a zr####; none excluded.
    monkeypatch.setattr(score_tags.reading_queue, "read_score_cache",
                        lambda: _cache(("A", 4.5, None, False), ("B", 3.9, None, False)))
    monkeypatch.setattr(score_tags.reading_queue, "_goal_affinity", lambda keys: {})
    items = [
        {"item_key": "B", "date_added": "2026-01-02"},   # scored 3.9
        {"item_key": "C", "date_added": "2026-01-03"},   # no abstract → not cached
        {"item_key": "A", "date_added": "2026-01-01"},   # scored 4.5
    ]
    writer = _Writer(running=False)
    monkeypatch.setattr(score_tags, "get_zotero_reader_or_raise", lambda: _reader(items))
    monkeypatch.setattr(score_tags, "get_zotero_writer_or_raise", lambda: writer)

    out = score_tags.sync_score_ranks()

    assert out["ranked"] == 3 and out["scored"] == 2 and out["unscored"] == 1
    assert out["field"] == "callNumber" and out["backup_path"] == "/tmp/zotero.bak"
    changes, create_backup = writer.calls[0]
    assert create_backup is True                       # backup-first
    order = [(c["item_key"], c["change_type"], c["payload_json"]) for c in changes]
    # A (4.5) and B (3.9) on top by relevance; C (no score) at the bottom, numbered.
    assert order == [
        ("A", "set_field", {"field": "callNumber", "value": "zr0001"}),
        ("B", "set_field", {"field": "callNumber", "value": "zr0002"}),
        ("C", "set_field", {"field": "callNumber", "value": "zr0003"}),
    ]


def test_sync_score_ranks_no_dedup_every_item_numbered(monkeypatch):
    # The whole-library write does NOT dedup — two items both receive a distinct
    # number, so the entire library is sortable in Zotero.
    monkeypatch.setattr(score_tags.reading_queue, "read_score_cache",
                        lambda: _cache(("A", 4.5, None, False), ("B", 4.5, None, False)))
    monkeypatch.setattr(score_tags.reading_queue, "_goal_affinity", lambda keys: {})
    items = [{"item_key": "A", "date_added": "2026-01-01"},
             {"item_key": "B", "date_added": "2026-01-02"}]
    writer = _Writer(running=False)
    monkeypatch.setattr(score_tags, "get_zotero_reader_or_raise", lambda: _reader(items))
    monkeypatch.setattr(score_tags, "get_zotero_writer_or_raise", lambda: writer)

    out = score_tags.sync_score_ranks()
    assert out["ranked"] == 2
    changes, _ = writer.calls[0]
    assert sorted(c["payload_json"]["value"] for c in changes) == ["zr0001", "zr0002"]


def test_sync_score_ranks_requires_force_when_zotero_running(monkeypatch):
    monkeypatch.setattr(score_tags.reading_queue, "read_score_cache",
                        lambda: _cache(("A", 4.0, None, False)))
    monkeypatch.setattr(score_tags.reading_queue, "_goal_affinity", lambda keys: {})
    monkeypatch.setattr(score_tags, "get_zotero_reader_or_raise",
                        lambda: _reader([{"item_key": "A", "date_added": ""}]))
    monkeypatch.setattr(score_tags, "get_zotero_writer_or_raise", lambda: _Writer(running=True))
    out = score_tags.sync_score_ranks(force=False)
    assert out.get("requires_force") is True


def test_sync_score_ranks_noop_without_scores(monkeypatch):
    monkeypatch.setattr(score_tags.reading_queue, "read_score_cache", lambda: {})
    out = score_tags.sync_score_ranks()
    assert out["ranked"] == 0 and "Rescore" in out["message"]
