"""Pin which legacy ``feed:<id>`` verdict keys ``current_feed_verdict`` accepts.

A sibling's legacy key counts only when the alias resolver maps it back to the
same stable key; an alias to another paper or an ambiguous id must not leak a
verdict. These scenarios guard the batched sibling resolution.
"""
from __future__ import annotations

import pytest

from tests.test_offline_sync import _db
from zotero_summarizer.storage import feeds, feeds_history


@pytest.fixture
def conn(tmp_path):
    with feeds.open_triage_conn(_db(tmp_path)) as c:
        yield c


def _row(conn, lib, item, stable, created="2026-01-01 00:00:00"):
    pk = feeds.record_decision(conn, run_id="r", feed_item={
        "feed_library_id": lib, "item_id": item, "guid": f"g-{lib}-{item}",
        "stable_feed_key": stable or "",
    }, decision=feeds.DECISION_TRIAGED_PENDING)
    conn.execute("UPDATE processed_feed_items SET stable_feed_key = ?, created_at = ? WHERE id = ?",
                 (stable, created, pk))
    return dict(conn.execute("SELECT * FROM processed_feed_items WHERE id = ?", (pk,)).fetchone())


def _verdict(conn, key, comment, created="2026-01-02 00:00:00"):
    conn.execute(
        "INSERT INTO label_verdicts (item_key, original_derived_priority, user_priority, comment, created_at)"
        " VALUES (?, 'should_read', 'must_read', ?, ?)", (key, comment, created))


def _alias(conn, item, stable):
    conn.execute("INSERT INTO feed_key_aliases (old_key, stable_feed_key) VALUES (?, ?)",
                 (f"feed:{item}", stable))


def _comment(conn, row):
    verdict = feeds.current_feed_verdict(conn, row)
    return verdict["comment"] if verdict else None


def test_a_plain_stable_key_one_row(conn):
    row = _row(conn, 1, 101, "feed:doi:a")
    assert _comment(conn, row) is None
    _verdict(conn, "feed:doi:a", "stable")
    assert _comment(conn, row) == "stable"
    _verdict(conn, "feed:101", "own-legacy", created="2026-01-03 00:00:00")
    assert _comment(conn, row) == "own-legacy"  # unaliased own id resolves back to this paper


def test_b_legacy_only_row(conn):
    row = _row(conn, 1, 111, None)
    row["guid"] = row["title"] = ""  # no computable stable key: the legacy key leads
    _verdict(conn, "feed:111", "legacy")
    assert _comment(conn, row) == "legacy"


def test_c_siblings_with_consistent_aliases_are_accepted(conn):
    first = _row(conn, 1, 201, "feed:doi:c")
    _row(conn, 2, 202, "feed:doi:c")
    _alias(conn, 201, "feed:doi:c")
    _alias(conn, 202, "feed:doi:c")
    _verdict(conn, "feed:202", "sibling")
    assert _comment(conn, first) == "sibling"


def test_d_alias_to_a_different_stable_key_is_rejected(conn):
    row = _row(conn, 1, 211, "feed:doi:d")
    _row(conn, 2, 212, "feed:doi:d")
    _row(conn, 3, 213, "feed:doi:d")
    _row(conn, 4, 99, "feed:doi:other")
    _alias(conn, 212, "feed:doi:other")  # target has a row
    _alias(conn, 213, "feed:doi:nowhere")  # target has no row
    _verdict(conn, "feed:212", "other-paper")
    _verdict(conn, "feed:213", "nowhere")
    assert _comment(conn, row) is None
    _verdict(conn, "feed:211", "own", created="2026-01-03 00:00:00")
    assert _comment(conn, row) == "own"


def test_e_ambiguous_legacy_id_is_rejected(conn):
    row = _row(conn, 1, 221, "feed:doi:e")
    _row(conn, 2, 222, "feed:doi:e")
    conn.execute("INSERT INTO feed_key_alias_ambiguities (old_key, stable_feed_keys_json, row_count)"
                 " VALUES ('feed:222', '[]', 2)")
    _verdict(conn, "feed:222", "ambiguous")
    assert _comment(conn, row) is None


def test_f_newest_revision_wins_across_sibling_legacy_keys(conn):
    row = _row(conn, 1, 231, "feed:doi:f")
    _row(conn, 2, 232, "feed:doi:f")
    _verdict(conn, "feed:doi:f", "stable-oldest")  # label inserts bump sync revisions in order
    _verdict(conn, "feed:231", "older")
    _verdict(conn, "feed:232", "newer")
    assert _comment(conn, row) == "newer"
    conn.execute("INSERT INTO sync_changes (item_key, field, value) VALUES ('feed:231', 'verdict', 'x')")
    assert _comment(conn, row) == "older"  # a sync revision outranks created_at


def test_g_newest_row_with_the_same_item_id_decides(conn):
    mine = _row(conn, 1, 251, "feed:doi:g", created="2026-01-01 00:00:00")
    _row(conn, 2, 251, "feed:doi:other-g", created="2026-01-05 00:00:00")
    _verdict(conn, "feed:251", "legacy")
    assert _comment(conn, mine) is None  # newer same-id row belongs to another paper


def test_h_same_item_id_tie_keeps_first_row(conn):
    first = _row(conn, 1, 261, "feed:doi:h1")
    second = _row(conn, 2, 261, "feed:doi:h2")  # identical created_at
    _verdict(conn, "feed:261", "legacy")
    assert (_comment(conn, first), _comment(conn, second)) == ("legacy", None)


def _fetch(conn, row):
    return dict(conn.execute("SELECT * FROM processed_feed_items WHERE id = ?", (row["id"],)).fetchone())


def test_pending_add_reuses_an_already_materialized_sibling(conn):
    row = _row(conn, 1, 271, "feed:doi:i")
    sibling = _row(conn, 2, 272, "feed:doi:i")
    conn.execute("UPDATE processed_feed_items SET materialized_zotero_key = 'ZKEY' WHERE id = ?", (sibling["id"],))
    conn.execute(
        "UPDATE processed_feed_items SET final_outcome = ?, outcome_signal_weight = 1.0,"
        " outcome_detected_at = '2026-01-01' WHERE id = ?", (feeds_history.OUTCOME_KEPT_UNREAD_APP, row["id"]))
    assert feeds_history.mark_pending_if_current(conn, row, "approved") is False
    linked = _fetch(conn, row)
    assert (linked["materialized_zotero_key"], linked["decision"], linked["decision_reason"]) == (
        "ZKEY", "selected", "reused_materialized_sibling")
    assert linked["zotero_sync_status"] == "synced"
    assert (linked["final_outcome"], linked["outcome_signal_weight"], linked["outcome_detected_at"]) == (None, None, None)


def test_sibling_link_never_overwrites_a_held_zotero_key(conn):
    row = _row(conn, 1, 281, "feed:doi:j")
    conn.execute("UPDATE processed_feed_items SET materialized_zotero_key = 'OWN' WHERE id = ?", (row["id"],))
    before = _fetch(conn, row)
    assert feeds_history.link_materialized_sibling(conn, row, "ZKEY") is False
    assert _fetch(conn, row) == before
