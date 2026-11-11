"""Production partition/persistence authority, with scripted external boundaries."""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from zotero_summarizer import runtime
from zotero_summarizer.integrations.app_rss import AppRssReader
from zotero_summarizer.models import GoalsConfig
from zotero_summarizer.services.triage import summarization
from zotero_summarizer.services.triage.feeds import _tick_phases as phases
from zotero_summarizer.settings import Settings
from zotero_summarizer.storage import feeds, rss

MARKER = "prefilter_low_corpus_affinity"


def _seed_feed(settings):
    with feeds.open_triage_conn(settings.triage_db_path) as conn:
        feed_id = rss.upsert_rss_feed(conn, name="Synthetic", url="https://example.invalid/rss")
        rss.upsert_rss_item(conn, rss_feed_id=feed_id, item={
            "guid": "synthetic-authority-case", "title": "Synthetic engineering note",
            "url": "https://hackernoon.com/synthetic-authority-case",
            "abstract": "Synthetic fixture prose, not evidence of practitioner usefulness.",
        })
        conn.commit()
    return AppRssReader(settings.triage_db_path).get_feed_items()[0]


def _exercise_case(tmp_path, monkeypatch, *, corpus_context, generated_tags, score=3,
                   erase_machine_tags=False):
    settings = Settings.load(project_root=tmp_path)
    config = GoalsConfig(
        research_goals=["Synthetic engineering"], triage_criteria=["Synthetic evidence"],
        relevance_scale={i: str(i) for i in range(1, 6)},
        llm={"draft_model": "scripted", "refine_model": "scripted",
             "api_base": "https://example.invalid/v1", "api_key_env": "SYNTHETIC_API_KEY"},
        prestige={"enabled": False},
    )
    runtime.set_context(runtime.AppContext(
        settings=settings, state=runtime.RuntimeState(app_state=SimpleNamespace(config=config)),
    ))
    llm = Mock()
    llm.prompt.return_value = json.dumps({"executive_summary": "Scripted successful summary"})
    llm.pydantic_prompt.return_value = json.dumps({
        "score": score, "reading_priority": "could_read", "tags": generated_tags,
        "rationale": "Scripted successful triage", "confidence": 0.5,
        "_corpus_fast_rejected": True, "corpus_fast_rejected": True,
    })
    match = Mock(return_value=corpus_context)
    monkeypatch.setattr(summarization.corpus, "run_corpus_match", match)
    if erase_machine_tags:
        real_fast_response = summarization._fast_reject_response

        def without_display_tag(*args):
            response = real_fast_response(*args)
            response.tags = []
            return response

        monkeypatch.setattr(summarization, "_fast_reject_response", without_display_tag)
    item = _seed_feed(settings)
    triaged, rejected, errors, fatal = phases.run_triage_stage(
        [item], tick_id="authority", gate_only=False, triage_llm=llm,
    )
    results = phases._TickResults(triaged, rejected, errors, [], [], [])
    phases.record_tick_decisions(results, tick_id="authority", review_mode=True)
    unprocessed, skipped, stale = phases.prepare_unprocessed([item], tick_id="next")
    marked = phases.mark_processed_read(
        phases._TickResults([], [], [], [], [], []), stale, writer=None, tick_id="next",
    )
    with feeds.open_triage_conn(settings.triage_db_path) as conn:
        row = dict(conn.execute("SELECT * FROM processed_feed_items").fetchone())
        source_row = dict(conn.execute("SELECT * FROM rss_items").fetchone())
    candidates = triaged + rejected
    summary = candidates[0][1].summary.model_dump(mode="json") if candidates else None
    return {
        "corpus_context": corpus_context, "threshold": config.corpus.similarity_threshold,
        "generated_tags": generated_tags, "model_score": score,
        "erase_machine_tags": erase_machine_tags,
        "partition": {"triaged": len(triaged), "fast_rejected": len(rejected),
                      "errors": errors, "fatal": fatal},
        "calls": {"corpus": match.call_count, "refine": llm.prompt.call_count,
                  "triage": llm.pydantic_prompt.call_count},
        "summary": summary, "persisted_row": row,
        "next_tick": {"unprocessed": len(unprocessed), "skipped": skipped,
                      "stale_to_mark": stale, "marked": marked},
        "rss_read_at": source_row["read_at"],
    }


def _retain_evidence(case_id, evidence, record_property):
    root = Path(__file__).resolve().parents[1]
    loaded = {}
    for name in ("zotero_summarizer.services.triage.feeds._triage",
                 "zotero_summarizer.services.triage.feeds._tick_phases",
                 "zotero_summarizer.services.triage.summarization",
                 "zotero_summarizer.models.triage"):
        module = sys.modules[name]
        path = Path(module.__file__).resolve()
        assert path.is_relative_to(root)
        loaded[name] = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    evidence["case_id"] = case_id
    evidence["loaded_modules"] = loaded
    evidence["scope"] = "scripted production phases, not a live daemon/model or semantic acceptance"
    record_property("prefilter_authority_evidence", json.dumps(evidence))
    output_root = os.environ.get("ZS_PREFILTER_TEST_EVIDENCE")
    if output_root:
        destination = Path(output_root) / f"{case_id}.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("x") as handle:
            json.dump(evidence, handle, indent=2)


@pytest.mark.parametrize("case", [
    ("generated-exact", False, 0.0, 3, MARKER),
    ("generated-infix", True, 0.8, 4, f"analysis:{MARKER.upper()}:not-a-gate"),
    ("generated-low-score-no-corpus", False, -0.9, 1, MARKER),
    ("generated-threshold-boundary", True, -0.30, 3, MARKER),
    ("ordinary-tag", True, 0.8, 3, "ordinary-topic"),
])
def test_successful_generated_tags_do_not_authorize_corpus_rejection(
    tmp_path, monkeypatch, record_property, case,
):
    case_id, has_corpus, affinity, score, tag = case
    evidence = _exercise_case(
        tmp_path, monkeypatch, corpus_context={"has_corpus": has_corpus, "affinity_score": affinity},
        generated_tags=[tag], score=score,
    )
    _retain_evidence(case_id, evidence, record_property)
    assert evidence["calls"] == {"corpus": 1, "refine": 1, "triage": 1}
    assert evidence["partition"]["errors"] == [] and evidence["partition"]["fatal"] is False
    assert evidence["summary"]["tags"] == [tag]
    assert evidence["summary"]["relevance_score"] == score
    assert evidence["summary"]["executive_summary"] == "Scripted successful summary"
    row = evidence["persisted_row"]
    assert (row["decision"], row["decision_reason"]) == (feeds.DECISION_AWAITING_REVIEW, "awaiting_review")
    assert evidence["partition"] == {"triaged": 1, "fast_rejected": 0, "errors": [], "fatal": False}
    assert row["error"] is None
    assert json.loads(row["shap_contribs_json"])["summary"] == evidence["summary"]
    assert "_corpus_fast_rejected" not in evidence["summary"]
    assert "corpus_fast_rejected" not in evidence["summary"]
    assert evidence["next_tick"] == {"unprocessed": 0, "skipped": 1, "stale_to_mark": [], "marked": 0}
    assert evidence["rss_read_at"] is None and row["read_time_marked_at"] is None


@pytest.mark.parametrize("erase_machine_tags", [False, True], ids=["original-tag", "no-display-tag"])
def test_real_machine_prefilter_rejects_without_model_calls_or_tag_authority(
    tmp_path, monkeypatch, record_property, erase_machine_tags,
):
    evidence = _exercise_case(
        tmp_path, monkeypatch, corpus_context={"has_corpus": True, "affinity_score": -0.31},
        generated_tags=[], erase_machine_tags=erase_machine_tags,
    )
    case_id = "machine-no-display-tag" if erase_machine_tags else "machine-original-tag"
    _retain_evidence(case_id, evidence, record_property)
    assert evidence["corpus_context"]["affinity_score"] < evidence["threshold"]
    assert evidence["calls"] == {"corpus": 1, "refine": 0, "triage": 0}
    assert evidence["partition"]["errors"] == [] and evidence["partition"]["fatal"] is False
    assert evidence["summary"]["tags"] == ([] if erase_machine_tags else [MARKER])
    row = evidence["persisted_row"]
    assert (row["decision"], row["decision_reason"]) == (feeds.DECISION_REJECTED_LOW_SCORE, "corpus_fast_reject")
    assert evidence["partition"] == {"triaged": 0, "fast_rejected": 1, "errors": [], "fatal": False}
    assert evidence["summary"]["reading_priority"] == "dont_read"
    assert row["error"] is None
    assert evidence["next_tick"]["marked"] == 1 and len(evidence["next_tick"]["stale_to_mark"]) == 1
    assert evidence["rss_read_at"] is not None and row["read_time_marked_at"] is not None
