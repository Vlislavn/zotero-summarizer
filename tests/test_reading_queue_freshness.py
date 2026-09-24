"""Cached fleet proposals are valid only for the deep review that produced them."""

from tests.test_reading_queue import (
    _FakeGate, _FakeReader, _item, _patch_state, _seed,
)
from tests._reading_queue_support import isolate
from zotero_summarizer.services.library import deep_review, reading_queue
from zotero_summarizer.services.library.review_fleet import verdict_store
import pytest


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    isolate(monkeypatch, tmp_path)
    yield
    reading_queue.finish(error=None)


def test_current_proposal_is_attached_without_changing_queue_order(monkeypatch):
    _patch_state(monkeypatch, _FakeReader([_item("A"), _item("B")]), _FakeGate("sha1"))
    _seed("sha1", A=3.0, B=4.0)
    identity = {"source_sha256": "pdf-a", "generation_sha256": "gen-a"}
    proposal = {
        "proposed": "must_read", "confidence": 0.85,
        "proposal_version": verdict_store.PROPOSAL_VERSION,
        "review_identity_sha256": verdict_store.review_fingerprint({"review_identity": identity}),
    }
    monkeypatch.setattr(verdict_store, "read_all", lambda: {"A": proposal})
    monkeypatch.setattr(deep_review, "current_reviews", lambda: {"A": {"review_identity": identity}})

    result = reading_queue.build_reading_queue()

    assert [row["item_key"] for row in result["items"]] == ["B", "A"]
    by_key = {row["item_key"]: row for row in result["items"]}
    assert by_key["A"]["proposed_verdict"] == proposal
    assert by_key["B"]["proposed_verdict"] is None


def test_changed_review_hides_stale_proposal_for_recomputation(monkeypatch):
    _patch_state(monkeypatch, _FakeReader([_item("A")]), _FakeGate("sha1"))
    _seed("sha1", A=3.0)
    old = {"source_sha256": "old-pdf", "generation_sha256": "gen-a"}
    current = {"source_sha256": "new-pdf", "generation_sha256": "gen-a"}
    monkeypatch.setattr(verdict_store, "read_all", lambda: {"A": {
        "proposed": "must_read", "proposal_version": verdict_store.PROPOSAL_VERSION,
        "review_identity_sha256": verdict_store.review_fingerprint({"review_identity": old}),
    }})
    monkeypatch.setattr(deep_review, "current_reviews", lambda: {"A": {"review_identity": current}})

    row = reading_queue.build_reading_queue()["items"][0]

    assert row["proposed_verdict"] is None
