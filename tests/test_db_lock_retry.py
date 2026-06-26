"""Phase 1.7: ZoteroWriter._retry_on_lock behaviour."""
from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock, call, patch

import pytest

from zotero_summarizer.integrations.zotero_write import ZoteroWriter, ZoteroWriteError


def _locked_then_ok(n_locked: int):
    """Return a factory that raises OperationalError(database is locked) n times then returns 42."""
    calls = [0]

    def fn():
        calls[0] += 1
        if calls[0] <= n_locked:
            raise sqlite3.OperationalError("database is locked")
        return 42

    return fn


def test_retry_succeeds_after_two_locks():
    """_retry_on_lock retries twice on lock and returns the value on the third attempt."""
    fn = _locked_then_ok(2)
    with patch("zotero_summarizer.integrations.zotero_write.time") as mock_time:
        result = ZoteroWriter._retry_on_lock(fn, max_retries=3, delay_secs=0.0)
    assert result == 42
    assert mock_time.sleep.call_count == 2


def test_retry_exhausted_reraises():
    """When locked more times than max_retries, re-raises the OperationalError."""
    fn = _locked_then_ok(4)  # 4 locked attempts, max_retries=3 → fails
    with patch("zotero_summarizer.integrations.zotero_write.time"):
        with pytest.raises(sqlite3.OperationalError, match="database is locked"):
            ZoteroWriter._retry_on_lock(fn, max_retries=3, delay_secs=0.0)


def test_retry_does_not_retry_non_lock_errors():
    """A non-lock OperationalError is NOT retried — propagates immediately."""
    call_count = [0]

    def fn():
        call_count[0] += 1
        raise sqlite3.OperationalError("no such table: foo")

    with patch("zotero_summarizer.integrations.zotero_write.time") as mock_time:
        with pytest.raises(sqlite3.OperationalError, match="no such table"):
            ZoteroWriter._retry_on_lock(fn, max_retries=3, delay_secs=0.0)

    assert call_count[0] == 1, "should have been called exactly once"
    mock_time.sleep.assert_not_called()


def test_retry_passes_through_non_operational_errors():
    """Non-sqlite3 exceptions are never retried."""

    def fn():
        raise ValueError("unexpected")

    with patch("zotero_summarizer.integrations.zotero_write.time") as mock_time:
        with pytest.raises(ValueError, match="unexpected"):
            ZoteroWriter._retry_on_lock(fn, max_retries=3, delay_secs=0.0)

    mock_time.sleep.assert_not_called()


def test_mark_feed_items_read_retries_on_lock(tmp_path):
    """mark_feed_items_read wraps _retry_on_lock; a transient lock is retried."""
    from zotero_summarizer.integrations.zotero_write import ZoteroWriter

    # Build a minimal writer pointing at a fake path (we mock the connection).
    fake_db = tmp_path / "zotero.sqlite"
    fake_db.touch()
    (tmp_path / "storage").mkdir()

    writer = ZoteroWriter(tmp_path)

    lock_attempts = [0]

    def fake_connect(path, *, timeout):
        lock_attempts[0] += 1
        if lock_attempts[0] == 1:
            raise sqlite3.OperationalError("database is locked")
        # Second call: return a mock connection that succeeds.
        conn = MagicMock()
        cursor = MagicMock()
        cursor.rowcount = 1
        conn.execute.return_value = cursor
        return conn

    with patch("zotero_summarizer.integrations.zotero_write.sqlite3.connect", side_effect=fake_connect):
        with patch("zotero_summarizer.integrations.zotero_write.time"):
            result = writer.mark_feed_items_read([100])

    assert result == 1
    assert lock_attempts[0] == 2
