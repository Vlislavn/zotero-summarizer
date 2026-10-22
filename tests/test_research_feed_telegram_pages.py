"""M1a: real selected-path SQLite; only nth-upsert failure is injected."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone

import pytest

from zotero_summarizer.models.research_feed import (
    TelegramPage, TelegramPost, normalize_telegram_username,
    validate_live_telegram_channel_id,
)
from zotero_summarizer.storage import migrations, repositories
from zotero_summarizer.storage import research_feed_telegram as telegram


A = -1000000000001
B = -1000000000002
TABLES = ("research_feed_telegram_channels", "research_feed_telegram_posts",
          "research_feed_telegram_bindings", "research_feed_telegram_account")
PAPER_TABLES = ("rss_feeds", "rss_items", "processed_feed_items", "triage_results",
                "label_verdicts", "pending_changes", "user_feedback")


def _post(message, channel=A, **changes):
    fields = dict(schema_version=1, channel_id=channel, message_id=message,
                  username="Example", permalink=f"https://example.test/{channel}/{message}",
                  text="  🧪 Подпись / caption\n原文  ", published_at="2026-10-02T12:00:00+03:00",
                  urls=["https://example.test/β", "https://example.test/a"])
    return TelegramPost(**(fields | changes))


def _page(*posts, complete=True):
    return TelegramPage(posts=posts, complete=complete)


@pytest.fixture
def store(tmp_path):
    path = tmp_path / "selected" / "triage.db"
    migrations.run_migrations(path, "triage", migrations.TRIAGE_MIGRATIONS)
    with closing(repositories._connect_to(path)) as conn:
        yield conn, path


def _bind(conn, username="Example", channel=A):
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        return telegram.bind_channel(conn, username=username, channel_id=channel)


def _commit(conn, page, *, username="Example", channel=A, expected=(False, None)):
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        return telegram.commit_page(conn, username=username, channel_id=channel,
                                    expected_state=expected, page=page)


def _state(conn, username="Example"):
    row = telegram.read_channel_state(conn, username=username)
    assert type(row["initial_window_committed"]) is bool
    return row["initial_window_committed"], row["confirmed_message_id"]


def _snapshot(conn):
    return {table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY 1, 2")]
            for table in TABLES}


def test_engineering_card_preserves_nonempty_missing_reproduction_inputs():
    from zotero_summarizer.models.research_feed import ResearchEngineeringCard

    card = ResearchEngineeringCard(
        source_id="fixture",
        problem="Reproduce the result",
        core_idea="Reuse the published implementation",
        engineering_novelty="Explicit reproduction inputs",
        reproducibility_tier="blocked",
        reproducibility_rationale="Weights are unavailable",
        missing_reproduction_inputs=["weights"],
        research_impact=1,
        production_impact=1,
        personal_novelty=1,
        worth_reading="unknown",
    )
    assert "missing_reproduction_inputs" in ResearchEngineeringCard.model_fields
    assert card.missing_reproduction_inputs == ["weights"]
    payload = card.model_dump_json()
    assert json.loads(payload)["missing_reproduction_inputs"] == ["weights"]
    assert ResearchEngineeringCard.model_validate_json(payload).missing_reproduction_inputs == ["weights"]


def test_existing_model_exports_unchanged_and_new_contracts_direct_only():
    from zotero_summarizer import models
    from zotero_summarizer.models import research_feed

    assert research_feed.__all__ == [
        "TOPIC_TAXONOMY", "ResearchCandidate", "ResearchEngineeringCard",
        "ResearchFeedTriage", "ResearchIdea", "ResearchProfile",
        "parse_run_budgets", "TelegramPost", "TelegramNewsVerdict",
    ]
    for name in ("TelegramPage", "LiveTelegramChannelId", "validate_live_telegram_channel_id",
                 "normalize_telegram_username"):
        assert hasattr(research_feed, name)
        assert name not in models.__all__
        assert not hasattr(models, name)


def test_v11_upgrade_preserves_original_evaluation_and_legacy_cursor(tmp_path):
    path = tmp_path / "triage.db"
    assert migrations.run_migrations(path, "triage", migrations.TRIAGE_MIGRATIONS[:11]) == 11
    with closing(repositories._connect_to(path)) as conn:
        with conn:
            telegram.upsert_post(conn, _post(7))
            row = telegram.list_posts(conn, limit=1)[0]
            assert telegram.save_evaluation(conn, row, {"summary": "not an original"})
            conn.execute("UPDATE research_feed_telegram_channels SET confirmed_message_id=7")
        original = tuple(conn.execute("SELECT * FROM research_feed_telegram_posts").fetchone())
    assert migrations.run_migrations(path, "triage", migrations.TRIAGE_MIGRATIONS) == migrations.SCHEMA_VERSION
    assert migrations.run_migrations(path, "triage", migrations.TRIAGE_MIGRATIONS) == migrations.SCHEMA_VERSION
    with closing(repositories._connect_to(path)) as conn:
        assert tuple(conn.execute("SELECT * FROM research_feed_telegram_posts").fetchone()) == original
        assert tuple(conn.execute("SELECT * FROM research_feed_telegram_channels").fetchone()) == (A, 7, 0)
        assert telegram.read_channel_state(conn, username="Example") is None
        with pytest.raises(ValueError, match="state"):
            _bind(conn)
        assert conn.execute("SELECT count(*) FROM research_feed_telegram_bindings").fetchone()[0] == 0
    corpus = tmp_path / "corpus.db"
    # Only the new no-op alignment step: no EmbeddingCache/weights or invented corpus schema.
    assert migrations.CORPUS_MIGRATIONS[-1].name == "combined_live_telegram_review_alignment"
    assert migrations.TRIAGE_MIGRATIONS[-1].name == "combined_live_telegram_review"
    assert migrations.run_migrations(corpus, "corpus", migrations.CORPUS_MIGRATIONS[-1:]) == migrations.SCHEMA_VERSION
    assert migrations.run_migrations(corpus, "corpus", migrations.CORPUS_MIGRATIONS[-1:]) == migrations.SCHEMA_VERSION
    with closing(repositories._connect_to(corpus)) as conn:
        assert [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")] == [
            "schema_migrations"]
    assert migrations.SCHEMA_VERSION == 13


def test_saved_upsert_stays_uninitialized_unbound_and_nonpaper(store):
    conn, path = store
    with conn:
        assert telegram.upsert_post(conn, _post(7, channel=101)) == "new"
        assert telegram.upsert_post(conn, _post(7, channel=101)) == "unchanged"
    assert tuple(conn.execute("SELECT confirmed_message_id, initial_window_committed "
                              "FROM research_feed_telegram_channels").fetchone()) == (None, 0)
    assert telegram.read_channel_state(conn, username="Example") is None
    assert telegram.read_retry_at(conn) is None
    assert conn.execute("SELECT count(*) FROM research_feed_telegram_bindings").fetchone()[0] == 0
    assert all(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0 for table in PAPER_TABLES)
    assert not (path.parent / "corpus_cache.db").exists()


@pytest.mark.parametrize("value,key", [(" @ExAmPle ", "example"), ("a", "a"), ("1A_", "1a_"),
                                      ("A" * 32, "a" * 32)])
def test_username_grammar_and_binding_aliases(store, value, key):
    conn, _ = store
    assert normalize_telegram_username(value) == key
    assert _bind(conn, value)
    before = _snapshot(conn)
    assert _bind(conn, "@" + key.upper())
    assert not _bind(conn, key, B)
    assert _snapshot(conn) == before
    assert telegram.read_channel_state(conn, username=key)["channel_id"] == A
    assert _commit(conn, _page(), username="@" + key.upper())["fetched"] == 0
    assert _state(conn, key) == (True, None)


@pytest.mark.parametrize("value", [None, True, 7, [], "", "@", "@@abc", "123", "___", "тест",
                                  "https://t.me/example", "+invite", "a/b", "a b", "a" * 33])
def test_invalid_username_all_boundaries_zero_sql(store, value):
    conn, _ = store
    statements = []
    conn.set_trace_callback(statements.append)
    try:
        with pytest.raises(ValueError, match="^invalid Telegram username$"):
            telegram.read_channel_state(conn, username=value)
        with pytest.raises(ValueError, match="^invalid Telegram username$"):
            telegram.bind_channel(conn, username=value, channel_id=A)
        with pytest.raises(ValueError, match="^invalid Telegram username$"):
            telegram.commit_page(conn, username=value, channel_id=A,
                                 expected_state=(False, None), page=_page())
        assert statements == []
    finally:
        conn.set_trace_callback(None)


@pytest.mark.parametrize("value", [True, False, 0, 101, -1, -1000000000000, -(2**63)-1, str(A), 1.5])
def test_strict_live_channel_id_no_sql(store, value):
    conn, _ = store
    statements = []
    conn.set_trace_callback(statements.append)
    try:
        with pytest.raises(ValueError, match="^invalid live Telegram channel ID$"):
            validate_live_telegram_channel_id(value)
        with pytest.raises(ValueError):
            telegram.bind_channel(conn, username="Example", channel_id=value)
        with pytest.raises(ValueError):
            telegram.commit_page(conn, username="Example", channel_id=value,
                                 expected_state=(False, None), page=_page())
        assert statements == []
    finally:
        conn.set_trace_callback(None)
    assert validate_live_telegram_channel_id(-(2**63)) == -(2**63)


def test_binding_parent_survives_no_fetch_reopen_and_refuses_rebinding(store):
    conn, path = store
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 0
    assert _bind(conn)
    assert _state(conn) == (False, None)
    with closing(repositories._connect_to(path)) as reopened:
        assert _state(reopened, "@EXAMPLE") == (False, None)
        before = _snapshot(reopened)
        assert not _bind(reopened, "example", B)
        assert _snapshot(reopened) == before
        assert reopened.execute("SELECT count(*) FROM research_feed_telegram_channels").fetchone()[0] == 1
        assert reopened.execute("SELECT count(*) FROM research_feed_telegram_posts").fetchone()[0] == 0


def test_writes_require_transaction_and_never_commit_caller_work(store):
    conn, _ = store
    with pytest.raises(ValueError, match="transaction"):
        telegram.bind_channel(conn, username="Example", channel_id=A)
    assert _bind(conn)
    with pytest.raises(ValueError, match="transaction"):
        telegram.commit_page(conn, username="Example", channel_id=A,
                             expected_state=(False, None), page=_page())
    with pytest.raises(ValueError, match="transaction"):
        telegram.defer_account(conn, retry_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    before = _snapshot(conn)
    conn.execute("BEGIN IMMEDIATE")
    assert telegram.bind_channel(conn, username="Other", channel_id=B)
    telegram.defer_account(conn, retry_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    telegram.commit_page(conn, username="Example", channel_id=A,
                         expected_state=(False, None), page=_page(_post(7)))
    assert conn.in_transaction
    conn.rollback()
    assert _snapshot(conn) == before


@pytest.mark.parametrize("expected", [(0, None), (1, None), (False, 1), (True, True), (True, 0),
                                     (True, -1), (True, 2**63), (True, "7"), [False, None], (False,), None])
def test_expected_state_strict_and_no_page_writes(store, expected):
    conn, _ = store
    _bind(conn)
    before = _snapshot(conn)
    with pytest.raises(ValueError, match="state"):
        _commit(conn, _page(), expected=expected)
    assert _snapshot(conn) == before


@pytest.mark.parametrize("kind", ["wrong_channel", "duplicate", "descending", "incomplete", "oversize", "untyped"])
def test_whole_invalid_page_has_no_partial_writes(store, kind):
    conn, _ = store
    _bind(conn)
    pages = {"wrong_channel": _page(_post(2), _post(4, B)),
             "duplicate": _page(_post(2), _post(2)), "descending": _page(_post(4), _post(2)),
             "incomplete": _page(_post(2), complete=False), "untyped": {"posts": (), "complete": True},
             "oversize": TelegramPage.model_construct(posts=tuple(_post(i) for i in range(1, 102)),
                                                      complete=True)}
    if kind == "oversize":
        with pytest.raises(ValueError):
            TelegramPage(posts=pages[kind].posts, complete=True)
    before = _snapshot(conn)
    with pytest.raises(ValueError):
        _commit(conn, pages[kind])
    assert _snapshot(conn) == before


@pytest.mark.parametrize("fields", [{"posts": ()}, {"posts": (), "complete": 1},
                                    {"posts": [], "complete": True},
                                    {"posts": (), "complete": True, "extra": 1}])
def test_page_contract_is_strict_with_required_completeness(fields):
    with pytest.raises(ValueError):
        TelegramPage(**fields)
    assert _page(complete=False).complete is False


@pytest.mark.parametrize("field,value", [("message_id", True), ("message_id", 0), ("message_id", 2**63),
                                        ("channel_id", True), ("channel_id", 0),
                                        ("schema_version", True), ("text", ""), ("skip_reason", " "),
                                        ("published_at", "2026-01-01"), ("permalink", "private:bad"),
                                        ("urls", ["ftp://example.test"]), ("username", 5)])
@pytest.mark.parametrize("bypassed", [False, True])
def test_current_canonical_fields_revalidated_even_for_corrupt_instances(store, field, value, bypassed):
    conn, _ = store
    _bind(conn)
    post = _post(2)
    page = _page(post)
    if bypassed:
        post = TelegramPost.model_construct(**(post.model_dump() | {field: value}))
        page = TelegramPage.model_construct(posts=(_post(1), post), complete=True)
    else:
        setattr(post, field, value)
    before = _snapshot(conn)
    with pytest.raises(ValueError, match="^invalid Telegram page$"):
        _commit(conn, page)
    assert _snapshot(conn) == before


def test_initial_hundred_overlap_unicode_hash_edit_invalidation_and_skips(store):
    conn, _ = store
    first, second, third = _post(1), _post(2), _post(3)
    with conn:
        telegram.upsert_post(conn, first)
        telegram.upsert_post(conn, second)
        telegram.upsert_post(conn, third)
        old = telegram.list_posts(conn, limit=3)
        for row in old:
            assert telegram.save_evaluation(conn, row, {"summary": "must not replace original"})
    old_json = conn.execute("SELECT post_json FROM research_feed_telegram_posts WHERE message_id=1").fetchone()[0]
    _bind(conn)
    edited = _post(2, username="Renamed", updated_at="2026-10-03T00:00:00Z",
                   urls=list(reversed(second.urls)))
    text_edit = _post(3, text="  🧪 Исправленная подпись\n原文  ")
    posts = (first, edited, text_edit, *(_post(i) for i in range(4, 100)),
             _post(100, text="", skip_reason="service"))
    counts = _commit(conn, _page(*posts))
    assert counts == {"fetched": 100, "new": 97, "updated": 2, "unchanged": 1, "skipped": 1}
    assert counts["new"] + counts["updated"] + counts["unchanged"] == counts["fetched"]
    assert _state(conn) == (True, 100)
    ordered = telegram.list_posts(conn, limit=100)
    assert [r["post"].message_id for r in ordered] == list(range(1, 101))
    rows = {r["post"].message_id: r for r in ordered}
    assert rows[1]["post"].model_dump() == first.model_dump()
    assert rows[2]["post"].model_dump() == edited.model_dump()
    assert rows[3]["post"].model_dump() == text_edit.model_dump()
    assert rows[1]["evaluation"] == {"summary": "must not replace original"}
    assert rows[2]["evaluation"] is None
    assert rows[3]["evaluation"] is None
    assert rows[2]["content_hash"] != old[1]["content_hash"]
    assert rows[3]["content_hash"] != old[2]["content_hash"]
    assert rows[100]["post"].skip_reason == "service"
    assert conn.execute("SELECT post_json FROM research_feed_telegram_posts WHERE message_id=1").fetchone()[0] == old_json
    assert json.loads(old_json)["text"] == first.text
    with conn:
        assert not telegram.save_evaluation(conn, old[1], {"summary": "stale"})


def test_empty_restart_sparse_composite_ids_and_expected_state_recheck(store):
    conn, path = store
    _bind(conn)
    assert _commit(conn, _page()) == dict(fetched=0, new=0, updated=0, unchanged=0, skipped=0)
    assert _state(conn) == (True, None)
    conn.close()
    with closing(repositories._connect_to(path)) as reopened:
        assert _state(reopened) == (True, None)
        _commit(reopened, _page(_post(7), _post(900, published_at="2000-01-01T00:00:00Z")),
                expected=(True, None))
    with closing(repositories._connect_to(path)) as conn:
        assert _state(conn) == (True, 900)
        assert telegram.list_posts(conn, limit=1)[0]["post"].message_id == 7
        assert _bind(conn, "Alias", A)
        assert _state(conn, "Alias") == (True, 900)
        before = _snapshot(conn)
        for expected in ((False, None), (True, None), (True, 899)):
            with pytest.raises(ValueError, match="stale"):
                _commit(conn, _page(), username="Alias", expected=expected)
        with pytest.raises(ValueError):
            _commit(conn, _page(_post(900)), expected=(True, 900))
        with pytest.raises(ValueError, match="binding"):
            _commit(conn, _page(), username="Missing", expected=(True, 900))
        with pytest.raises(ValueError, match="binding"):
            _commit(conn, _page(), channel=B, expected=(True, 900))
        assert _snapshot(conn) == before
        _commit(conn, _page(), expected=(True, 900))
        _commit(conn, _page(_post(1001)), expected=(True, 900))
        assert _bind(conn, "Other", B)
        _commit(conn, _page(_post(7, B)), username="Other", channel=B)
        assert _state(conn) == (True, 1001)
        assert _state(conn, "Other") == (True, 7)
        assert {r["post"].source_id for r in telegram.list_posts(conn, limit=10)} == {
            f"telegram:{A}:7", f"telegram:{A}:900", f"telegram:{A}:1001", f"telegram:{B}:7"}


@pytest.mark.parametrize("initialized", [False, True])
def test_nth_upsert_rollback_retry_and_earlier_transaction_survives(store, monkeypatch, initialized):
    conn, path = store
    _bind(conn)
    if initialized:
        _commit(conn, _page(_post(1)))
    expected = (True, 1) if initialized else (False, None)
    before = _snapshot(conn)
    real_upsert = telegram.upsert_post
    calls = []

    def fail_second(connection, post):
        result = real_upsert(connection, post)
        calls.append(post.message_id)
        if len(calls) == 2:
            raise RuntimeError("injected nth-upsert failure")
        return result

    with monkeypatch.context() as patch:
        patch.setattr(telegram, "upsert_post", fail_second)
        with pytest.raises(RuntimeError, match="nth-upsert"):
            _commit(conn, _page(_post(2), _post(9)), expected=expected)
    assert calls == [2, 9]
    assert _snapshot(conn) == before
    with closing(repositories._connect_to(path)) as reopened:
        assert _snapshot(reopened) == before
        assert _commit(reopened, _page(_post(2), _post(9)), expected=expected)["new"] == 2
        assert _state(reopened) == (True, 9)
        committed = _snapshot(reopened)
        with pytest.raises(ValueError):
            _commit(reopened, _page(_post(2), _post(9)), expected=expected)
        assert _snapshot(reopened) == committed


def test_schema_checks_and_explicit_null_account_deadline(store):
    conn, _ = store
    with conn:
        conn.execute("INSERT INTO research_feed_telegram_account(slot, retry_at) VALUES (1, NULL)")
    assert telegram.read_retry_at(conn) is None
    with pytest.raises(sqlite3.IntegrityError):
        with conn:
            conn.execute("INSERT INTO research_feed_telegram_account(slot) VALUES (2)")
    _bind(conn)
    with pytest.raises(sqlite3.IntegrityError):
        with conn:
            conn.execute("UPDATE research_feed_telegram_channels SET initial_window_committed=2")
    assert _state(conn) == (False, None)


def test_corrupt_state_fails_loud_without_repair(store):
    conn, _ = store
    _bind(conn)
    with conn:
        conn.execute("UPDATE research_feed_telegram_channels SET confirmed_message_id=3")
    before = _snapshot(conn)
    with pytest.raises(ValueError, match="state"):
        telegram.read_channel_state(conn, username="Example")
    with pytest.raises(ValueError, match="state"):
        _bind(conn)
    with pytest.raises(ValueError, match="state"):
        _commit(conn, _page())
    assert _snapshot(conn) == before
    with conn:
        conn.execute("DELETE FROM research_feed_telegram_channels")
    with pytest.raises(ValueError, match="state"):
        telegram.read_channel_state(conn, username="Example")


def test_retry_max_utc_reopen_no_expiry_clear_or_implicit_commit(store):
    conn, path = store
    assert telegram.read_retry_at(conn) is None
    later = datetime(2026, 10, 2, 12, 0, 0, 123456, timezone(timedelta(hours=3)))
    canonical = "2026-10-02T09:00:00.123456+00:00"
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        assert telegram.defer_account(conn, retry_at=later) == canonical
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        assert telegram.defer_account(conn, retry_at=later - timedelta(seconds=1)) == canonical
    with closing(repositories._connect_to(path)) as reopened:
        assert telegram.read_retry_at(reopened) == canonical
        assert datetime.fromisoformat(canonical) == later.astimezone(timezone.utc)
        # Storage leaves even an exact/expired deadline intact; M1b owns admission.
        assert telegram.read_retry_at(reopened) == canonical
    before = _snapshot(conn)
    conn.execute("BEGIN IMMEDIATE")
    assert telegram.defer_account(conn, retry_at=later + timedelta(days=1)) != canonical
    conn.rollback()
    assert _snapshot(conn) == before
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        newest = (later + timedelta(microseconds=1)).astimezone(timezone.utc).isoformat()
        assert telegram.defer_account(conn, retry_at=later + timedelta(microseconds=1)) == newest
    assert telegram.read_retry_at(conn) == newest


@pytest.mark.parametrize("value", [None, True, "2026-01-01T00:00:00Z", datetime(2026, 1, 1),
                                  datetime.min.replace(tzinfo=timezone(timedelta(hours=1)))])
def test_invalid_retry_input_no_writes(store, value):
    conn, _ = store
    before = _snapshot(conn)
    with pytest.raises(ValueError):
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            telegram.defer_account(conn, retry_at=value)
    assert _snapshot(conn) == before


@pytest.mark.parametrize("value", ["bad-private-time", "2026-01-01T00:00:00", "2026-01-01T00:00:00Z",
                                  "2026-01-01T01:00:00+01:00", "2026-01-01T00:00:00.000000+00:00"])
def test_corrupt_persisted_retry_not_default_ready_or_repaired(store, value):
    conn, _ = store
    with conn:
        conn.execute("INSERT INTO research_feed_telegram_account(slot, retry_at) VALUES (1, ?)", (value,))
    before = _snapshot(conn)
    with pytest.raises(ValueError, match="^invalid stored Telegram retry deadline$"):
        telegram.read_retry_at(conn)
    with pytest.raises(ValueError, match="^invalid stored Telegram retry deadline$"):
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            telegram.defer_account(conn, retry_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert _snapshot(conn) == before
