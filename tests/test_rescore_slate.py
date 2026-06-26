"""In-place Today-slate re-score after a gate upgrade.

Covers: (1) the percentile-first ``row_prestige`` source; (2) ``update_scores``
touching only the gate-derived columns (never the decision); (3) the
``rescore_slate`` orchestrator re-ranking the live slate while leaving handled
items and decisions untouched and plumbing ``citation_percentile`` into the
payload so Today's prestige reflects the new signal.
"""
from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

from zotero_summarizer.services.triage import rescore_slate as rs
from zotero_summarizer.services.triage.daily_select._candidate import row_prestige
from zotero_summarizer.storage import feeds as feeds_storage
from zotero_summarizer.storage import repositories as repo


# --- row_prestige: percentile-first --------------------------------------

def test_row_prestige_prefers_citation_percentile():
    # percentile (already [0,1]) wins over both the LLM prestige and h-index.
    payload = {
        "aux_context": {"citation_percentile": 0.9, "max_author_h_index": 80},
        "summary": {"prestige_score": 2.0},
    }
    assert row_prestige({}, payload) == 0.9


def test_row_prestige_falls_back_when_no_percentile():
    # No percentile → old behaviour (LLM prestige / 5).
    payload = {"aux_context": {"max_author_h_index": 80}, "summary": {"prestige_score": 5.0}}
    assert row_prestige({}, payload) == 1.0


# --- storage.update_scores: only score fields, never the decision ---------

def _conn(tmp_path):
    db = tmp_path / "triage.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    repo.apply_schema(conn)
    conn.commit()
    return db, conn


def test_update_scores_preserves_decision_and_read_status(tmp_path):
    db, conn = _conn(tmp_path)
    try:
        rid = feeds_storage.record_decision(
            conn, run_id="t",
            feed_item={"feed_library_id": 1, "item_id": 1, "guid": "g1", "title": "T",
                       "abstract": "abc", "publication_date": "2024"},
            decision=feeds_storage.DECISION_TRIAGED_PENDING,
            composite_score=2.0, reading_priority="could_read",
            shap_contribs_json=json.dumps({"summary": {"authors": "A"}}),
        )
        conn.commit()
        ok = feeds_storage.update_scores(
            conn, row_id=rid, composite_score=4.6, reading_priority="should_read",
            shap_contribs_json=json.dumps({"aux_context": {"citation_percentile": 0.9}}),
        )
        conn.commit()
        assert ok
        row = feeds_storage.get_processed_feed_item_by_pk(conn, rid)
        assert row["composite_score"] == 4.6
        assert row["reading_priority"] == "should_read"
        # Decision is UNTOUCHED — handled state can't change.
        assert row["decision"] == feeds_storage.DECISION_TRIAGED_PENDING
    finally:
        conn.close()


# --- rescore_slate orchestrator -------------------------------------------

class _FakePred:
    def __init__(self, item_key, score, priority, pct):
        self.item_key = item_key
        self.calibrated_score = score / 5.0   # rescore multiplies back by 5
        self.predicted_priority = priority
        self.shap_contribs = [{"feature": "prestige_score", "contribution": 0.1}]
        self.aux_context = {"citation_percentile": pct, "max_author_h_index": 10}


class _FakeGate:
    golden_csv_sha256 = "newsha"

    def __init__(self, mapping):
        self.mapping = mapping

    def predict(self, items, *, corpus_db_path, goals_config, return_shap):
        return [_FakePred(it["item_key"], *self.mapping[it["item_key"]]) for it in items]


def _seed_row(conn, *, item_id, guid, decision, composite, priority):
    rid = feeds_storage.record_decision(
        conn, run_id="t",
        feed_item={"feed_library_id": 1, "item_id": item_id, "guid": guid, "title": f"T{item_id}",
                   "abstract": "a real abstract long enough to score", "publication_date": "2024"},
        decision=decision, composite_score=composite, reading_priority=priority,
        shap_contribs_json=json.dumps({
            "shap": [], "aux_context": {"max_author_h_index": 5},
            "summary": {"authors": "A. Author", "triage_rationale": "kept rationale"},
        }),
    )
    conn.commit()
    return rid


def test_rescore_slate_reranks_in_place_and_skips_handled(tmp_path, monkeypatch):
    db, conn = _conn(tmp_path)
    try:
        # Two slate rows + one handled (labelled) row that must NOT be rescored.
        r1 = _seed_row(conn, item_id=1, guid="g1", decision=feeds_storage.DECISION_TRIAGED_PENDING,
                       composite=2.0, priority="could_read")
        r2 = _seed_row(conn, item_id=2, guid="g2", decision=feeds_storage.DECISION_AWAITING_REVIEW,
                       composite=3.0, priority="could_read")
        r3 = _seed_row(conn, item_id=3, guid="g3", decision=feeds_storage.DECISION_TRIAGED_PENDING,
                       composite=2.5, priority="could_read")
    finally:
        conn.close()
    # g3 is handled (priority label) → dropped by the same filter the slate uses.
    repo.insert_or_update_label_verdict(
        db, item_key="feed:3", original_derived_priority="could_read",
        user_priority="dont_read", comment="",
    )

    gate = _FakeGate({
        "g1": (4.6, "should_read", 0.95),   # promoted + high percentile
        "g2": (1.5, "dont_read", 0.05),     # demoted + low percentile
    })
    monkeypatch.setattr(rs, "get_settings", lambda: SimpleNamespace(
        triage_db_path=db, corpus_db_path=tmp_path / "corpus.db"))
    monkeypatch.setattr(rs, "get_state", lambda: SimpleNamespace(
        classifier_gate=gate, app_state=SimpleNamespace(config=SimpleNamespace())))

    out = rs.rescore_slate()
    assert out["rescored"] == 2          # g1 + g2; g3 handled → excluded
    assert out["gate_sha"] == "newsha"

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        g1 = feeds_storage.get_processed_feed_item_by_pk(conn, r1)
        g2 = feeds_storage.get_processed_feed_item_by_pk(conn, r2)
        g3 = feeds_storage.get_processed_feed_item_by_pk(conn, r3)
    finally:
        conn.close()

    # g1: composite = calibrated*5 = 4.6, priority updated, percentile plumbed,
    # decision + LLM rationale preserved.
    assert g1["composite_score"] == 4.6
    assert g1["reading_priority"] == "should_read"
    assert g1["decision"] == feeds_storage.DECISION_TRIAGED_PENDING
    payload = json.loads(g1["shap_contribs_json"])
    assert payload["aux_context"]["citation_percentile"] == 0.95
    assert payload["summary"]["triage_rationale"] == "kept rationale"   # preserved
    assert row_prestige({}, payload) == 0.95                            # Today prestige uses it

    # g2 re-ranked down but decision unchanged (no auto gate-reject).
    assert g2["composite_score"] == 1.5
    assert g2["decision"] == feeds_storage.DECISION_AWAITING_REVIEW

    # g3 handled → untouched (old score kept).
    assert g3["composite_score"] == 2.5
    assert g3["reading_priority"] == "could_read"


def test_rescore_slate_no_gate_is_message_noop(tmp_path, monkeypatch):
    db, conn = _conn(tmp_path)
    conn.close()
    monkeypatch.setattr(rs, "get_settings", lambda: SimpleNamespace(
        triage_db_path=db, corpus_db_path=tmp_path / "corpus.db"))
    monkeypatch.setattr(rs, "get_state", lambda: SimpleNamespace(
        classifier_gate=None, app_state=SimpleNamespace(config=SimpleNamespace())))
    out = rs.rescore_slate()
    assert out["rescored"] == 0
    assert "restart" in out["message"].lower()
