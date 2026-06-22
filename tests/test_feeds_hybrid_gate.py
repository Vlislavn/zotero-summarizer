"""Phase 1.13: hybrid daemon gate inside ``run_daemon_tick``.

These tests verify the new partition between dedup and the existing LLM
triage loop. The actual classifier (which is heavy and tested elsewhere)
is replaced by a hand-rolled stub that returns deterministic priorities.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import patch

from zotero_summarizer.services.triage.feeds import _gate as feeds
from zotero_summarizer.storage import feeds as feeds_storage


@dataclass
class _StubPrediction:
    """Mimics classifier.FeedPrediction's shape for the fields the gate uses."""

    item_key: str
    predicted_priority: str
    calibrated_score: float = 0.1
    raw_score: float = 0.1
    shap_contribs: list | None = None
    aux_context: dict | None = None


class _StubGate:
    """Fake TrainedClassifier — partitions items by predicted_priority."""

    def __init__(self, priority_by_key: dict[str, str], classifier_name: str = "tabpfn"):
        self.priority_by_key = priority_by_key
        self.classifier_name = classifier_name
        self.golden_csv_sha256 = "abc123def456"
        self.training_metadata = {"n_train": 100, "oof_auc": 0.8}

    def predict(self, items, *, corpus_db_path, goals_config, return_shap=False):
        out = []
        for it in items:
            key = str(it.get("item_key") or it.get("item_id") or "")
            priority = self.priority_by_key.get(key, "should_read")
            out.append(_StubPrediction(item_key=key, predicted_priority=priority))
        return out


def _make_items(*priorities: tuple[str, str]):
    """Build minimal feed-item dicts. Each (item_key, _) pair becomes a row."""
    return [
        {
            "item_id": int(k.lstrip("KP")) if k.lstrip("KP").isdigit() else 0,
            "item_key": k,
            "title": f"Title {k}",
            "abstract": "abstract text " * 30,
            "doi": "",
            "arxiv_id": "",
            "feed_library_id": 2,
            "guid": k,
        }
        for k, _ in priorities
    ]


# ---------------------------------------------------------------------------
# _apply_classifier_gate (the partition primitive)
# ---------------------------------------------------------------------------


def test_apply_gate_returns_input_unchanged_when_no_gate_loaded():
    """Without a configured gate, the daemon must run unchanged."""
    items = _make_items(("K1", "must_read"))
    with patch.object(feeds, "get_state", return_value=SimpleNamespace(classifier_gate=None)):
        survivors, rejected = feeds._apply_classifier_gate("tick_test", items)
    assert survivors == items
    assert rejected == []


def test_apply_gate_partitions_dont_read_items():
    """Items predicted ``dont_read`` go to ``rejected``; the rest survive."""
    items = _make_items(
        ("K1", "must_read"), ("K2", "dont_read"),
        ("K3", "could_read"), ("K4", "dont_read"),
    )
    gate = _StubGate({
        "K1": "must_read",
        "K2": "dont_read",
        "K3": "could_read",
        "K4": "dont_read",
    })
    fake_state = SimpleNamespace(
        classifier_gate=gate,
        app_state=SimpleNamespace(config=SimpleNamespace(
            classifier_gate=SimpleNamespace(drop_priorities=["dont_read"], raw_score_dont_read_below=0.0, audit_sample_per_tick=0),
        )),
    )
    fake_settings = SimpleNamespace(corpus_db_path="/tmp/nonexistent.db")
    with patch.object(feeds, "get_state", return_value=fake_state), \
         patch.object(feeds, "get_settings", return_value=fake_settings):
        survivors, rejected = feeds._apply_classifier_gate("tick_test", items)

    survivor_keys = {it["item_key"] for it in survivors}
    rejected_keys = {it["item_key"] for it, _ in rejected}
    assert survivor_keys == {"K1", "K3"}
    assert rejected_keys == {"K2", "K4"}
    # Survivors get _gate_priority + _gate_score attached for downstream logging.
    for it in survivors:
        assert it["_gate_priority"] in {"must_read", "should_read", "could_read"}


def test_apply_gate_aggressive_drop_policy():
    """``drop_priorities = ['dont_read', 'could_read']`` rejects both."""
    items = _make_items(
        ("K1", "must_read"), ("K2", "could_read"), ("K3", "dont_read"),
    )
    gate = _StubGate({
        "K1": "must_read", "K2": "could_read", "K3": "dont_read",
    })
    fake_state = SimpleNamespace(
        classifier_gate=gate,
        app_state=SimpleNamespace(config=SimpleNamespace(
            classifier_gate=SimpleNamespace(drop_priorities=["dont_read", "could_read"], raw_score_dont_read_below=0.0, audit_sample_per_tick=0),
        )),
    )
    fake_settings = SimpleNamespace(corpus_db_path="/tmp/nonexistent.db")
    with patch.object(feeds, "get_state", return_value=fake_state), \
         patch.object(feeds, "get_settings", return_value=fake_settings):
        survivors, rejected = feeds._apply_classifier_gate("tick_test", items)

    assert [it["item_key"] for it in survivors] == ["K1"]
    assert sorted(it["item_key"] for it, _ in rejected) == ["K2", "K3"]


def test_apply_gate_empty_drop_set_returns_everything():
    """Empty drop list = effectively disabled even though a gate is loaded."""
    items = _make_items(("K1", "dont_read"))
    gate = _StubGate({"K1": "dont_read"})
    fake_state = SimpleNamespace(
        classifier_gate=gate,
        app_state=SimpleNamespace(config=SimpleNamespace(
            classifier_gate=SimpleNamespace(drop_priorities=[], raw_score_dont_read_below=0.0, audit_sample_per_tick=0),
        )),
    )
    fake_settings = SimpleNamespace(corpus_db_path="/tmp/nonexistent.db")
    with patch.object(feeds, "get_state", return_value=fake_state), \
         patch.object(feeds, "get_settings", return_value=fake_settings):
        survivors, rejected = feeds._apply_classifier_gate("tick_test", items)
    assert survivors == items
    assert rejected == []


def test_apply_gate_propagates_predict_errors():
    """A broken predict must NOT be swallowed — daemon must visibly fail."""
    class _BrokenGate(_StubGate):
        def predict(self, items, **kw):
            raise RuntimeError("model is corrupted")

    gate = _BrokenGate({})
    fake_state = SimpleNamespace(
        classifier_gate=gate,
        app_state=SimpleNamespace(config=SimpleNamespace(
            classifier_gate=SimpleNamespace(drop_priorities=["dont_read"], raw_score_dont_read_below=0.0, audit_sample_per_tick=0),
        )),
    )
    fake_settings = SimpleNamespace(corpus_db_path="/tmp/nonexistent.db")
    items = _make_items(("K1", "must_read"))
    with patch.object(feeds, "get_state", return_value=fake_state), \
         patch.object(feeds, "get_settings", return_value=fake_settings):
        import pytest as _pytest

        with _pytest.raises(RuntimeError, match="corrupted"):
            feeds._apply_classifier_gate("tick_test", items)


# ---------------------------------------------------------------------------
# gate_only: items the gate can't score must not crash the drain
# ---------------------------------------------------------------------------


class _FilteringStubGate(_StubGate):
    """Mirrors the real gate: returns NO prediction for items lacking title+abstract
    (the case left after the OpenAlex backfill can't recover an abstract)."""

    def predict(self, items, *, corpus_db_path, goals_config, return_shap=False):
        out = []
        for it in items:
            if not ((it.get("title") or "").strip() and (it.get("abstract") or "").strip()):
                continue
            key = str(it.get("item_key") or it.get("item_id") or "")
            out.append(_StubPrediction(
                item_key=key,
                predicted_priority=self.priority_by_key.get(key, "should_read"),
            ))
        return out


def _gate_state(gate):
    return SimpleNamespace(
        classifier_gate=gate,
        app_state=SimpleNamespace(config=SimpleNamespace(
            classifier_gate=SimpleNamespace(
                drop_priorities=["dont_read"], raw_score_dont_read_below=0.0,
                audit_sample_per_tick=0,
            ),
        )),
    )


def _mixed_items():
    """Two scorable items (K1, K3) + one with an empty abstract (K2, unscorable)."""
    return [
        {"item_id": 1, "item_key": "K1", "title": "Title 1",
         "abstract": "abstract text " * 30, "doi": "", "feed_library_id": 2, "guid": "K1"},
        {"item_id": 45492, "item_key": "K2", "title": "Title 2 no abstract",
         "abstract": "", "doi": "", "feed_library_id": 2, "guid": "K2"},
        {"item_id": 3, "item_key": "K3", "title": "Title 3",
         "abstract": "abstract text " * 30, "doi": "", "feed_library_id": 2, "guid": "K3"},
    ]


def _patched_gate(gate):
    settings = SimpleNamespace(corpus_db_path="/tmp/nonexistent.db")
    return patch.object(feeds, "get_state", return_value=_gate_state(gate)), \
        patch.object(feeds, "get_settings", return_value=settings)


def test_gate_only_routes_unscorable_to_rejected_not_survivors():
    items = _mixed_items()
    gate = _FilteringStubGate({"K1": "should_read", "K3": "must_read"})
    p_state, p_settings = _patched_gate(gate)
    with p_state, p_settings:
        survivors, rejected = feeds._apply_classifier_gate("tick", items, gate_only=True)
    assert {it["item_key"] for it in survivors} == {"K1", "K3"}
    for it in survivors:
        assert it.get("_gate_prediction") is not None
    # The no-abstract item is a terminal gate-reject with pred=None (not a survivor).
    assert [(it["item_key"], pred) for it, pred in rejected] == [("K2", None)]


def test_gate_only_unscorable_does_not_reach_synthesiser():
    """Regression: the no-abstract item used to crash run_triage_stage(gate_only)."""
    from zotero_summarizer.services.triage.feeds._tick_phases import run_triage_stage

    items = _mixed_items()
    gate = _FilteringStubGate({"K1": "should_read", "K3": "must_read"})
    p_state, p_settings = _patched_gate(gate)
    with p_state, p_settings:
        survivors, _rejected = feeds._apply_classifier_gate("tick", items, gate_only=True)
    # Must NOT raise — every survivor carries a prediction.
    triaged, fast, errors, fatal = run_triage_stage(
        survivors, tick_id="tick", gate_only=True, triage_llm=None,
    )
    assert {it["item_key"] for it, _ in triaged} == {"K1", "K3"}
    assert fast == [] and errors == [] and fatal is False


def test_non_gate_only_forwards_unscorable_to_survivors():
    """The LLM path still receives no-abstract items (it handles them itself)."""
    items = _mixed_items()
    gate = _FilteringStubGate({"K1": "should_read", "K3": "must_read"})
    p_state, p_settings = _patched_gate(gate)
    with p_state, p_settings:
        survivors, rejected = feeds._apply_classifier_gate("tick", items, gate_only=False)
    assert "K2" in {it["item_key"] for it in survivors}
    assert rejected == []


def test_record_tick_decisions_persists_unscorable_gate_reject(tmp_path, monkeypatch):
    """A gate_rejected entry with pred=None records a terminal gate-reject row with
    a clear reason and no score — no crash on the missing prediction."""
    import sqlite3
    from zotero_summarizer.services.triage.feeds import _common, _tick_phases
    from zotero_summarizer.services.triage.feeds._tick_phases import _TickResults

    db = tmp_path / "triage.db"
    monkeypatch.setattr(_common, "get_settings", lambda: SimpleNamespace(triage_db_path=db))
    item = {"item_id": 45492, "item_key": "K9", "title": "No abstract paper",
            "abstract": "", "doi": "", "feed_library_id": 2, "guid": "K9"}
    results = _TickResults(
        triaged=[], fast_rejected=[], errors=[],
        gate_rejected=[(item, None)], library_skipped=[], processed_dup_skipped=[],
    )
    _tick_phases.record_tick_decisions(results, tick_id="tick", review_mode=False)

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT decision, decision_reason FROM processed_feed_items WHERE feed_item_id=?",
            (45492,),
        ).fetchone()
    finally:
        conn.close()
    assert row["decision"] == feeds_storage.DECISION_GATE_REJECTED
    assert row["decision_reason"] == "gate_unscorable:no_abstract"


# ---------------------------------------------------------------------------
# Decision constant
# ---------------------------------------------------------------------------


def test_gate_rejected_decision_constant_exists():
    """The new decision string is exported so downstream code can use it."""
    assert feeds_storage.DECISION_GATE_REJECTED == "gate_rejected"


# ---------------------------------------------------------------------------
# _maybe_schedule_gate_retrain
# ---------------------------------------------------------------------------


def test_retrain_not_scheduled_when_sha_matches(tmp_path):
    """If golden CSV sha == cached gate sha → no retrain triggered."""
    from zotero_summarizer.services import run_log

    golden = tmp_path / "zotero-summarizer-golden.csv"
    golden.write_text("item_key,gold_priority_final\nP1,must_read\n", encoding="utf-8")
    current_sha = run_log.file_sha256(golden, prefix_len=64)

    gate = _StubGate({})
    gate.golden_csv_sha256 = current_sha
    fake_state = SimpleNamespace(
        classifier_gate=gate,
        classifier_gate_training=False,
    )
    fake_settings = SimpleNamespace(project_root=tmp_path, golden_csv_path=golden)
    with patch.object(feeds, "get_state", return_value=fake_state), \
         patch.object(feeds, "get_settings", return_value=fake_settings), \
         patch("threading.Thread") as mock_thread:
        feeds._maybe_schedule_gate_retrain("tick_test")
    mock_thread.assert_not_called()
    assert fake_state.classifier_gate_training is False


def test_retrain_scheduled_when_sha_differs(tmp_path):
    """sha mismatch + no training in progress → background thread is started."""
    golden = tmp_path / "zotero-summarizer-golden.csv"
    golden.write_text("item_key,gold_priority_final\nP1,must_read\n", encoding="utf-8")

    gate = _StubGate({})
    gate.golden_csv_sha256 = "different_sha_value"
    fake_state = SimpleNamespace(
        classifier_gate=gate,
        classifier_gate_training=False,
    )
    fake_settings = SimpleNamespace(project_root=tmp_path, golden_csv_path=golden)
    with patch.object(feeds, "get_state", return_value=fake_state), \
         patch.object(feeds, "get_settings", return_value=fake_settings), \
         patch("threading.Thread") as mock_thread:
        feeds._maybe_schedule_gate_retrain("tick_test")
    mock_thread.assert_called_once()
    assert fake_state.classifier_gate_training is True


def test_retrain_skipped_when_already_in_progress(tmp_path):
    """If another retrain is running, do not spawn a second one."""
    golden = tmp_path / "zotero-summarizer-golden.csv"
    golden.write_text("item_key,gold_priority_final\nP1,must_read\n", encoding="utf-8")

    gate = _StubGate({})
    gate.golden_csv_sha256 = "different_sha"
    fake_state = SimpleNamespace(
        classifier_gate=gate,
        classifier_gate_training=True,
    )
    fake_settings = SimpleNamespace(project_root=tmp_path, golden_csv_path=golden)
    with patch.object(feeds, "get_state", return_value=fake_state), \
         patch.object(feeds, "get_settings", return_value=fake_settings), \
         patch("threading.Thread") as mock_thread:
        feeds._maybe_schedule_gate_retrain("tick_test")
    mock_thread.assert_not_called()
