"""Tests for Phase 1.17 Step 3 — :func:`next_audit_for_today`.

Covers the daily-trickle audit picker: empty pool, no-prior-responses,
rate-limit gating, bucket priority by least-answered, side-effect on
``last_trickle_emitted_at``, and within-day determinism.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from zotero_summarizer.services import relabel_audit
from zotero_summarizer.services.relabel_audit._constants import AuditCandidate


_NOW = datetime(2026, 5, 15, 12, 0, 0, tzinfo=timezone.utc)


def _make_candidate(item_key: str, *, bucket: str = "90-180") -> AuditCandidate:
    return AuditCandidate(
        item_key=item_key,
        title=f"Title {item_key}",
        authors="J. Doe",
        venue="Nature",
        abstract="Some abstract",
        days_since_added=120 if bucket == "90-180" else 200,
        age_bucket=bucket,
        original_priority="should_read",
        original_inferred_relevance=4.0,
    )


def _write_session(
    session_path: Path, candidates: list[AuditCandidate]
) -> None:
    relabel_audit.write_session(
        session_path, candidates, sample_size=len(candidates), seed=42
    )


def _set_response(
    session_path: Path,
    item_key: str,
    *,
    timestamp_iso: str,
    new_priority: str = "should_read",
) -> None:
    """Inject a synthetic response into an existing session file."""
    text = session_path.read_text(encoding="utf-8")
    session = json.loads(text)
    session.setdefault("responses", {})[item_key] = {
        "new_priority": new_priority,
        "new_relevance": 4.0,
        "timestamp_iso": timestamp_iso,
    }
    session_path.write_text(
        json.dumps(session, indent=2, ensure_ascii=False), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_trickle_returns_empty_when_no_candidates(tmp_path: Path) -> None:
    session_path = tmp_path / "audit.json"
    # Manually write a session with empty candidates (write_session requires >=1).
    session_path.write_text(
        json.dumps(
            {
                "type": "relabel_audit_session",
                "version": 1,
                "created_at": "2026-05-15T00:00:00Z",
                "sample_size": 0,
                "seed": 42,
                "candidates": [],
                "responses": {},
            }
        ),
        encoding="utf-8",
    )
    out = relabel_audit.next_audit_for_today(session_path, now=_NOW)
    assert out == []


def test_trickle_returns_candidates_when_no_prior_responses(tmp_path: Path) -> None:
    session_path = tmp_path / "audit.json"
    cands = [_make_candidate(f"K{i}") for i in range(5)]
    _write_session(session_path, cands)
    out = relabel_audit.next_audit_for_today(session_path, max_per_day=2, now=_NOW)
    assert 1 <= len(out) <= 2
    chosen_keys = {c.item_key for c in out}
    assert chosen_keys.issubset({c.item_key for c in cands})


def test_trickle_respects_rate_limit_24h(tmp_path: Path) -> None:
    session_path = tmp_path / "audit.json"
    cands = [_make_candidate(f"K{i}") for i in range(5)]
    _write_session(session_path, cands)
    one_hour_ago = (_NOW - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    _set_response(session_path, "K0", timestamp_iso=one_hour_ago)
    out = relabel_audit.next_audit_for_today(session_path, max_per_day=2, now=_NOW)
    assert out == []


def test_trickle_emits_after_24h_passed(tmp_path: Path) -> None:
    session_path = tmp_path / "audit.json"
    cands = [_make_candidate(f"K{i}") for i in range(5)]
    _write_session(session_path, cands)
    long_ago = (_NOW - timedelta(hours=25)).isoformat().replace("+00:00", "Z")
    _set_response(session_path, "K0", timestamp_iso=long_ago)
    out = relabel_audit.next_audit_for_today(session_path, max_per_day=2, now=_NOW)
    assert 1 <= len(out) <= 2
    # The already-answered K0 must not be re-served.
    assert all(c.item_key != "K0" for c in out)


def test_trickle_writes_last_trickle_emitted_at(tmp_path: Path) -> None:
    session_path = tmp_path / "audit.json"
    cands = [_make_candidate(f"K{i}") for i in range(3)]
    _write_session(session_path, cands)
    before = json.loads(session_path.read_text(encoding="utf-8"))
    assert "last_trickle_emitted_at" not in before
    out = relabel_audit.next_audit_for_today(session_path, max_per_day=2, now=_NOW)
    assert out  # non-empty
    after = json.loads(session_path.read_text(encoding="utf-8"))
    assert "last_trickle_emitted_at" in after
    # Smoke-check that the stored timestamp parses as ISO-8601.
    stamp = after["last_trickle_emitted_at"]
    parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None


def test_trickle_picks_from_least_answered_bucket_first(tmp_path: Path) -> None:
    session_path = tmp_path / "audit.json"
    cands = [
        _make_candidate("A0", bucket="90-180"),
        _make_candidate("A1", bucket="90-180"),
        _make_candidate("B0", bucket="180-365"),
        _make_candidate("B1", bucket="180-365"),
        _make_candidate("C0", bucket="365-730"),
        _make_candidate("D0", bucket=">730"),
    ]
    _write_session(session_path, cands)
    # Cover "90-180" heavily, all timestamped >24h ago so they don't trigger rate-limit.
    long_ago = (_NOW - timedelta(hours=30)).isoformat().replace("+00:00", "Z")
    # Answer 5 distinct "90-180" items by re-using the existing A0/A1 keys via
    # synthetic injection — but each key needs a candidate, so we instead
    # add 5 covered candidates from the same bucket and pre-answer them.
    extra_covered = [_make_candidate(f"covered_{i}", bucket="90-180") for i in range(5)]
    all_cands = cands + extra_covered
    _write_session(session_path, all_cands)
    for c in extra_covered:
        _set_response(session_path, c.item_key, timestamp_iso=long_ago)
    out = relabel_audit.next_audit_for_today(session_path, max_per_day=1, now=_NOW)
    assert len(out) == 1
    # The "90-180" bucket has 5 answered, every other bucket has 0. The
    # picker prefers the bucket with the fewest answers, ties broken alpha —
    # so "180-365" is the next alphabetically; ">730" comes before letters
    # in ASCII. Either way the chosen item must NOT be from "90-180".
    assert out[0].age_bucket != "90-180"


def test_trickle_deterministic_within_day(tmp_path: Path) -> None:
    session_a = tmp_path / "a.json"
    session_b = tmp_path / "b.json"
    cands = [_make_candidate(f"K{i}") for i in range(8)]
    _write_session(session_a, cands)
    _write_session(session_b, cands)
    out_a = relabel_audit.next_audit_for_today(session_a, max_per_day=2, now=_NOW)
    out_b = relabel_audit.next_audit_for_today(session_b, max_per_day=2, now=_NOW)
    assert [c.item_key for c in out_a] == [c.item_key for c in out_b]


def test_trickle_rejects_invalid_max_per_day(tmp_path: Path) -> None:
    session_path = tmp_path / "audit.json"
    cands = [_make_candidate(f"K{i}") for i in range(3)]
    _write_session(session_path, cands)
    with pytest.raises(ValueError, match="max_per_day"):
        relabel_audit.next_audit_for_today(session_path, max_per_day=0, now=_NOW)
