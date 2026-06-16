"""Unit tests for the review-fleet's atomic JSON sidecar ``verdict_store``.

The store is the fleet's ONLY persistence: a ``proposed_verdicts.json`` keyed by
``item_key`` under the model dir. These tests exercise ``read_all`` / ``upsert`` /
``clear`` against a real-but-temporary dir (the path is redirected via the
module-level ``_cache_path``, so no app state and no model dir are touched).

Covered:
  - read_all on a missing file -> {} (the fleet has not run yet);
  - upsert creates + round-trips, and a second upsert UPSERTS (replaces) in place;
  - clear removes and reports whether it removed;
  - the write is atomic (tmp + replace) and leaves no stray ``.tmp`` behind;
  - the fail-loud boundaries: empty item_key raises, a corrupt file raises out of
    json.loads (a corrupt cache is a signal, not a silent empty).
"""
from __future__ import annotations

import json

import pytest

from zotero_summarizer.services.library.review_fleet import verdict_store


@pytest.fixture
def store_dir(tmp_path, monkeypatch):
    """Redirect the sidecar into a tmp dir so nothing touches the real model dir."""
    path = tmp_path / "proposed_verdicts.json"
    monkeypatch.setattr(verdict_store, "_cache_path", lambda: path)
    return path


def _proposal(verdict="should_read", confidence=0.7):
    return {
        "proposed": verdict,
        "confidence": confidence,
        "rationale": "the digest says read it",
        "flags": [],
        "digest_read_decision": "read",
        "grade": "B",
        "proposed_at": "2026-06-16T00:00:00Z",
        "source": "review_fleet",
    }


# --- read_all: empty on a fresh store ----------------------------------------------


def test_read_all_empty_when_file_missing(store_dir):
    assert not store_dir.exists()
    assert verdict_store.read_all() == {}


# --- upsert: create, round-trip, replace -------------------------------------------


def test_upsert_creates_file_and_round_trips(store_dir):
    proposal = _proposal("must_read", 0.85)
    verdict_store.upsert("ITEM1", proposal)

    assert store_dir.exists()
    all_proposals = verdict_store.read_all()
    assert all_proposals == {"ITEM1": proposal}
    # and the on-disk envelope carries the cache idiom (updated_at + proposals)
    payload = json.loads(store_dir.read_text(encoding="utf-8"))
    assert set(payload) == {"updated_at", "proposals"}
    assert payload["proposals"]["ITEM1"] == proposal


def test_upsert_second_key_keeps_the_first(store_dir):
    verdict_store.upsert("A", _proposal("must_read"))
    verdict_store.upsert("B", _proposal("could_read"))
    out = verdict_store.read_all()
    assert set(out) == {"A", "B"}
    assert out["A"]["proposed"] == "must_read"
    assert out["B"]["proposed"] == "could_read"


def test_upsert_same_key_replaces_in_place(store_dir):
    verdict_store.upsert("A", _proposal("could_read", 0.5))
    verdict_store.upsert("A", _proposal("must_read", 0.9))
    out = verdict_store.read_all()
    assert len(out) == 1
    assert out["A"]["proposed"] == "must_read"
    assert out["A"]["confidence"] == 0.9


def test_upsert_rejects_empty_item_key(store_dir):
    with pytest.raises(ValueError, match="item_key"):
        verdict_store.upsert("", _proposal())
    assert not store_dir.exists()  # nothing written on the bad call


# --- clear: remove + report --------------------------------------------------------


def test_clear_removes_existing_and_returns_true(store_dir):
    verdict_store.upsert("A", _proposal())
    verdict_store.upsert("B", _proposal())
    assert verdict_store.clear("A") is True
    out = verdict_store.read_all()
    assert set(out) == {"B"}


def test_clear_missing_key_returns_false_and_leaves_store(store_dir):
    verdict_store.upsert("A", _proposal())
    assert verdict_store.clear("NOPE") is False
    assert set(verdict_store.read_all()) == {"A"}


def test_clear_rejects_empty_item_key(store_dir):
    with pytest.raises(ValueError, match="item_key"):
        verdict_store.clear("")


# --- atomicity + corruption is a loud signal ---------------------------------------


def test_write_is_atomic_no_tmp_left_behind(store_dir):
    verdict_store.upsert("A", _proposal())
    verdict_store.upsert("A", _proposal("must_read"))
    leftovers = list(store_dir.parent.glob("*.tmp"))
    assert leftovers == []  # tmp.replace leaves no partial file


def test_read_all_raises_on_corrupt_file(store_dir):
    store_dir.write_text("{ this is not json", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        verdict_store.read_all()


def test_read_all_tolerates_missing_proposals_envelope_key(store_dir):
    """A valid-JSON file lacking the ``proposals`` key reads as {} (not a crash —
    ``read_all`` returns ``payload.get('proposals') or {}``)."""
    store_dir.write_text(json.dumps({"updated_at": "2026-06-16T00:00:00Z"}), encoding="utf-8")
    assert verdict_store.read_all() == {}
