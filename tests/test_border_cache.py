"""Regression tests for the border-suggestions cache + background-job
state (services.border_cache).

Root cause these guard against: border-suggestions used to retrain the
LightGBM model AND score ~740 library rows synchronously on every call,
taking >10 minutes (an effective timeout that surfaced as "border has
only 1 article" from a stale frontend cache). The fix made it a
cached, background-computed resource keyed by the golden CSV sha.
"""
from __future__ import annotations

import json
from pathlib import Path

from zotero_summarizer.services.library import border_cache


def test_read_cache_absent_returns_none(tmp_path: Path):
    assert border_cache.read_cache(tmp_path, "sha123") is None


def test_write_then_read_roundtrip(tmp_path: Path):
    items = [{"item_key": "K1", "border_distance": 0.01}]
    payload = border_cache.write_cache(tmp_path, "sha123", items)
    assert payload["golden_sha"] == "sha123"
    assert payload["total"] == 1
    assert "computed_at" in payload

    got = border_cache.read_cache(tmp_path, "sha123")
    assert got is not None
    assert got["items"] == items
    assert got["total"] == 1


def test_read_cache_stale_sha_returns_none(tmp_path: Path):
    border_cache.write_cache(tmp_path, "OLD_sha", [{"item_key": "K1"}])
    # A different golden sha must miss — the model/data changed.
    assert border_cache.read_cache(tmp_path, "NEW_sha") is None


def test_write_is_atomic_no_tmp_left(tmp_path: Path):
    border_cache.write_cache(tmp_path, "sha", [{"item_key": "K1"}])
    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == []
    assert (tmp_path / "border_suggestions.json").exists()


def test_corrupt_cache_raises_not_silently_swallowed(tmp_path: Path):
    (tmp_path / "border_suggestions.json").write_text("{not valid json", encoding="utf-8")
    import pytest
    with pytest.raises(json.JSONDecodeError):
        border_cache.read_cache(tmp_path, "sha")


def test_job_state_single_slot():
    """try_start claims the single compute slot; a second caller is refused
    until finish() releases it."""
    # Ensure clean state (other tests may have run).
    border_cache.finish(error=None)
    assert border_cache.is_running() is False

    assert border_cache.try_start() is True
    assert border_cache.is_running() is True
    # Second concurrent claim refused.
    assert border_cache.try_start() is False

    border_cache.finish(error=None)
    assert border_cache.is_running() is False
    assert border_cache.last_error() is None


def test_job_state_records_error():
    border_cache.finish(error=None)
    border_cache.try_start()
    border_cache.finish(error="boom")
    assert border_cache.is_running() is False
    assert border_cache.last_error() == "boom"
    # Starting again clears the prior error.
    border_cache.try_start()
    assert border_cache.last_error() is None
    border_cache.finish(error=None)
