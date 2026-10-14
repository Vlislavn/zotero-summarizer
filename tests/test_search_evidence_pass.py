"""Outside-in shadow-pass contracts; scripted replies test plumbing, not science.

Only existing pipeline/session APIs are imported. The injected namespace specifies
an additive request contract, so today's missing pass fails assertions, not import.
Generated review caches are intentionally outside the supplied-original contract.
"""
from __future__ import annotations

import hashlib
import json
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from zotero_summarizer.services.library import _review_cache
from zotero_summarizer.services.search import materialize, pipeline, session
from zotero_summarizer.services.search._models import Candidate, QueryPlan, ResearchSession, SearchIntent

IDS = tuple(f"P{n:02}" for n in range(1, 12))
QUESTION = "What impact did adding the step have on the measured outcome?"
CANONICAL = "A different canonical restatement must not replace the submitted question."
QUOTE = "Across the registered trials, adding the step did not improve the measured outcome."
PREFIX, BETWEEN = "Prelude.\n", "\nBetween the repeated passages.\n"
TEXT = PREFIX + QUOTE + BETWEEN + QUOTE + "\nEnd."
SECOND = len(PREFIX + QUOTE + BETWEEN)
PRIVATE_COMMENT = "PRIVATE_CONTEXT_ONLY: Нет. This comment is not paper evidence."


def _digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


@pytest.fixture(autouse=True)
def _loaded_sources(record_property):
    root = Path(__file__).resolve().parents[1]
    for leaf in ("pipeline", "session", "_models", "rank", "review", "_evidence_pass", "_source_context"):
        module = sys.modules.get(f"zotero_summarizer.services.search.{leaf}")
        if module is not None:
            path = Path(module.__file__).resolve()
            assert path.is_relative_to(root)
            record_property(module.__name__, f"{path}:{hashlib.sha256(path.read_bytes()).hexdigest()}")


def _snapshot(candidate_id):
    source_id = f"synthetic-original:{candidate_id}"
    version = f"{source_id}:v1"
    passages = [{"passage_id": f"occurrence-{n}", "start": start, "end": start + len(QUOTE), "quote": QUOTE}
                for n, start in enumerate((len(PREFIX), SECOND))]
    return {"candidate_id": candidate_id, "source_id": source_id, "source_version_id": version,
            "source_sha256": _digest(TEXT), "text": TEXT, "complete": True, "truncated": False,
            "source_kind": "synthetic_original", "retrieval_mode": "supplied_occurrence_spans",
            "checked_scope": {"start": 0, "end": len(TEXT)}, "passages": passages}


class _ScriptedPass:
    def __init__(self):
        self.events, self.generated, self.verified, self.effects = [], [], [], []
        self.sources = {key: _snapshot(key) for key in IDS}
        self.context = {"availability": "available_nonempty", "records": [
            {"source_id": "synthetic-app-note:7", "role": "comment", "revision": 7,
             "authorship": "explicit_user", "text": PRIVATE_COMMENT}]}
        self.state, self.proposal_fault, self.verifier_fault = "sufficient", None, None
        self.generator_error = self.verifier_error = None
        self.on_generate = None
        self.request = SimpleNamespace(
            enabled=True, available=True, candidate_ids=IDS, load_snapshot=self.load_snapshot,
            load_context=self.load_context, generator=self.generate, verifier=self.verify,
            generator_id="approved-synthetic-generator-v1", verifier_id="approved-synthetic-verifier-v1",
            max_calls=22, max_passages=2, max_chars=len(TEXT), schema_version="source-shadow-v1",
        )

    def load_snapshot(self, candidate_id):
        self.events.append(("snapshot", candidate_id))
        return deepcopy(self.sources[candidate_id])

    def load_context(self, candidate_id):
        self.events.append(("context", candidate_id))
        return deepcopy(self.context)

    def generate(self, *, question, question_index, source, context):
        self.events.append(("generate", source["candidate_id"]))
        self.generated.append(deepcopy({"question": question, "question_index": question_index,
                                       "source": source, "context": context}))
        if self.on_generate is not None:
            self.on_generate(source["candidate_id"])
        if self.generator_error is not None:
            raise self.generator_error
        locator = {**deepcopy(source["passages"][1]), "source_id": source["source_id"],
                   "source_version_id": source["source_version_id"]}
        proposal = {"answer": "Scripted answer; no semantic accuracy claim.", "evidence": [locator]}
        if self.proposal_fault == "missing_answer":
            proposal.pop("answer")
        elif self.proposal_fault == "extra_status":
            proposal["state"] = "sufficient"
        elif self.proposal_fault == "wrong_source":
            other = "P01" if source["candidate_id"] != "P01" else "P11"
            locator["source_id"] = self.sources[other]["source_id"]
        elif self.proposal_fault is not None:
            locator.update(self.proposal_fault)
        return proposal

    def verify(self, *, question, question_index, source, proposal):
        self.events.append(("verify", source["candidate_id"]))
        self.verified.append(deepcopy({"question": question, "question_index": question_index,
                                      "source": source, "proposal": proposal}))
        if self.verifier_error is not None:
            raise self.verifier_error
        reply = {"state": self.state, "supported": self.state in {"sufficient", "partial"},
                 "evidence_ids": ["occurrence-1"],
                 "uncovered": ["A scripted aspect is unchecked."] if self.state == "partial" else []}
        if self.verifier_fault is not None:
            reply.update(self.verifier_fault)
        return reply


@pytest.fixture
def case(monkeypatch, tmp_path):
    scripted = _ScriptedPass()
    monkeypatch.setattr(session, "settings", lambda: SimpleNamespace(search_dir=tmp_path / "sessions"))
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    monkeypatch.setattr(_review_cache, "settings", lambda: SimpleNamespace(model_dir=model_dir))
    scripted.cache = model_dir / "deep_reviews.json"

    def forbidden(*args, **kwargs):
        scripted.effects.append((args, kwargs))
        raise AssertionError("No generated-cache/model/provider/Zotero effect belongs to this pass")

    monkeypatch.setattr(_review_cache, "_read_all", forbidden)
    monkeypatch.setattr(_review_cache, "get_current_review", forbidden)
    monkeypatch.setattr(materialize, "ZoteroWriter", forbidden)
    monkeypatch.setattr(pipeline, "default_deps", forbidden)
    monkeypatch.setattr(pipeline, "federate", forbidden)
    monkeypatch.setattr(pipeline, "score_query_relevance", forbidden)
    legacy_llm = SimpleNamespace(prompt=forbidden, pydantic_prompt=forbidden)
    scripted.deps = SimpleNamespace(llm=legacy_llm, llm_light=legacy_llm, extractor=None,
                                   unpaywall_client=None, max_chars=len(TEXT), config=None)

    def acquire(candidate, **kwargs):
        scripted.events.append(("legacy_acquire", candidate.candidate_id))
        return scripted.sources[candidate.candidate_id]["text"]

    def light(candidate, **kwargs):
        scripted.events.append(("light", candidate.candidate_id))
        assert kwargs["full_text"] == scripted.sources[candidate.candidate_id]["text"]

    def targeted(candidate, **kwargs):
        scripted.events.append(("targeted", candidate.candidate_id))

    select = pipeline.select_deep_set

    def observed_select(candidates, **kwargs):
        scripted.events.append(("select", tuple(c.candidate_id for c in candidates)))
        return select(candidates, **kwargs)

    monkeypatch.setattr(pipeline, "acquire_full_text", acquire)
    monkeypatch.setattr(pipeline, "light_review", light)
    monkeypatch.setattr(pipeline, "targeted_review", targeted)
    monkeypatch.setattr(pipeline, "select_deep_set", observed_select)
    return scripted


def _run(case, *, request="enabled", session_id="shadow", questions=(QUESTION,), limits=(10, 4)):
    candidates = [Candidate(title="Identical metadata", candidate_id=key, doi=f"10.1000/{key.lower()}",
                            query_score=(11 - n) / 11, quality={"grade": "B", "quality_band": "neutral"},
                            review={"state": "preserved", "brief": {"tldr": key}}, verdict="skim",
                            materialized_zotero_key="SAVED001" if n == 0 else "")
                  for n, key in enumerate(IDS)]
    stored = ResearchSession(id=session_id, created_at="2026-10-08T00:00:00Z", raw_query="topic",
                             intent=SearchIntent(raw_query="topic", canonical_question=CANONICAL),
                             plan=QueryPlan(arxiv="public topic only"), questions=list(questions),
                             candidates=candidates, status="reviewing")
    session.save(stored)
    deps = SimpleNamespace(**vars(case.deps))
    if request != "absent":
        deps.evidence_pass = case.request if request == "enabled" else None
    pipeline.run_review(stored.id, deps=deps, top_n=limits[0], k=limits[1])
    return session.load(stored.id)


def _receipt(result, candidate_id="P11"):
    row = next(c.to_dict() for c in result.candidates if c.candidate_id == candidate_id)
    evidence = row.get("question_evidence", [])
    assert len(evidence) == 1, "Missing persisted pre-cutoff source-contract receipt"
    return evidence[0]


def _baseline_projection(result):
    data = result.to_dict()
    data.pop("id")
    for row in data["candidates"]:
        row.pop("question_evidence", None)
    return data


def test_shadow_assesses_explicit_eleven_before_cutoff_and_preserves_baseline(case):
    baseline = _run(case, request="absent", session_id="baseline")
    case.events.clear()

    result = _run(case)

    assert [key for stage, key in case.events if stage == "snapshot"] == list(IDS)
    first_light = next(i for i, event in enumerate(case.events) if event[0] == "light")
    assert case.events.index(("verify", "P11")) < first_light
    assert case.events.index(("verify", "P11")) < next(i for i, e in enumerate(case.events) if e[0] == "select")
    assert not any(stage in {"legacy_acquire", "targeted"} and key == "P11" for stage, key in case.events)
    assert _baseline_projection(result) == _baseline_projection(baseline)
    assert case.effects == []
    assert len(case.generated) == len(case.verified) == 11
    assert [call["source"]["candidate_id"] for call in case.generated] == list(IDS)
    assert [call["source"]["candidate_id"] for call in case.verified] == list(IDS)
    assert all(_receipt(result, key)["candidate_id"] == key for key in IDS)
    assert _receipt(result)["state"] == "sufficient"


def test_receipt_retains_exact_second_occurrence_question_and_source_version(case):
    result = _run(case)

    receipt = _receipt(result)
    source = case.sources["P11"]
    assert receipt["candidate_id"] == "P11"
    assert (receipt["question_index"], receipt["question"], receipt["question_id"]) == (0, QUESTION, _digest(QUESTION))
    assert receipt["canonical_question_id"] == _digest(CANONICAL)
    assert (receipt["source_id"], receipt["source_version_id"], receipt["source_sha256"]) == (
        source["source_id"], source["source_version_id"], source["source_sha256"])
    assert receipt["checked_scope"] == source["checked_scope"]
    assert receipt["evidence"] == [{**source["passages"][1], "source_id": source["source_id"],
                                    "source_version_id": source["source_version_id"]}]
    assert receipt["generator_id"] == case.request.generator_id
    assert receipt["verifier_id"] == case.request.verifier_id
    assert ResearchSession.from_dict(result.to_dict()).to_dict() == result.to_dict()
    assert all(call["question"] == QUESTION for call in case.generated + case.verified)


@pytest.mark.parametrize("state", ["sufficient", "partial", "not_found_in_checked_text", "unknown"])
def test_scripted_states_are_distinct_without_word_grading(case, state):
    case.state = state

    result = _run(case)

    assert _receipt(result)["state"] == state
    assert _receipt(result)["checked_scope"] == case.sources["P11"]["checked_scope"]
    assert len(case.verified) == 11
    if state == "partial":
        assert _receipt(result)["uncovered"] == ["A scripted aspect is unchecked."]


@pytest.mark.parametrize("fault,reason", [({"complete": False}, "source_partial"),
                                          ({"truncated": True}, "source_truncated")])
def test_incomplete_original_cannot_become_a_checked_negative(case, fault, reason):
    case.sources["P11"].update(fault)
    case.state = "not_found_in_checked_text"

    result = _run(case)

    receipt = _receipt(result)
    assert (receipt["state"], receipt["reason"]) == ("unknown", reason)
    assert not any(call["source"]["candidate_id"] == "P11" for call in case.verified)


@pytest.mark.parametrize("fault,reason", [
    ({"start": -1}, "invalid_locator"), ({"start": True}, "invalid_locator"),
    ({"end": len(TEXT) + 1}, "invalid_locator"), ({"quote": "Not original paper bytes"}, "invalid_locator"),
    ({"start": len(PREFIX), "end": len(PREFIX) + len(QUOTE)}, "invalid_locator"),
    ("wrong_source", "invalid_locator"),
    ({"source_version_id": "synthetic-original:P11:v2"}, "invalid_locator"),
    ({"passage_id": "synthetic-app-note:7", "quote": PRIVATE_COMMENT}, "invalid_locator"),
    ("missing_answer", "invalid_schema"), ("extra_status", "invalid_schema"),
])
def test_invalid_generated_contract_is_rejected_before_semantic_verifier(case, fault, reason):
    case.proposal_fault = fault

    result = _run(case)

    receipt = _receipt(result)
    assert (receipt["state"], receipt["reason"]) == ("unknown", reason)
    assert case.generated and case.verified == []
    assert case.effects == []


@pytest.mark.parametrize("fault", [{"state": "invented"}, {"supported": "true"}, {"evidence_ids": ["other"]}])
def test_malformed_verifier_reply_cannot_publish_a_semantic_status(case, fault):
    case.verifier_fault = fault

    result = _run(case)

    assert (_receipt(result)["state"], _receipt(result)["reason"]) == ("unknown", "invalid_schema")
    assert len(case.generated) == len(case.verified) == 11


@pytest.mark.parametrize("stage", ["generator", "verifier"])
def test_fake_provider_failure_is_unknown_not_a_scientific_negative(case, stage):
    setattr(case, stage + "_error", RuntimeError("Synthetic unavailable client"))

    result = _run(case)

    assert (_receipt(result)["state"], _receipt(result)["reason"]) == ("unknown", stage + "_unavailable")
    assert len(case.generated) <= 11 and len(case.verified) <= 11
    assert case.effects == []


def _assert_private_comment_excluded(payload):
    assert PRIVATE_COMMENT not in json.dumps(payload, ensure_ascii=False), "Private comment leaked"


@pytest.mark.parametrize("target", ["source", "verifier"])
def test_privacy_oracle_rejects_deliberately_contaminated_payloads(target):
    source = _snapshot("P11")
    payload = source if target == "source" else {"source": source, "proposal": {"answer": "Synthetic answer"}}
    _assert_private_comment_excluded(payload)
    if target == "source":
        source["text"] += "\n" + PRIVATE_COMMENT
    else:
        payload["proposal"]["answer"] = PRIVATE_COMMENT

    with pytest.raises(AssertionError, match="Private comment leaked"):
        _assert_private_comment_excluded(payload)


def test_private_comments_stay_context_and_never_original_evidence(case):
    result = _run(case)

    receipt = _receipt(result)
    assert len(case.generated) == len(case.verified) == 11
    assert all(call["context"] == case.context for call in case.generated)
    for call in case.generated:
        _assert_private_comment_excluded(call["source"])
    for call in case.verified:
        _assert_private_comment_excluded(call)
    assert all(row["source_id"].startswith("synthetic-original:") for row in receipt["evidence"])
    assert receipt["context_availability"] == "available_nonempty"
    assert receipt["used_context_ids"] == []  # Supplied does not mean used.
    assert case.effects == []


@pytest.mark.parametrize("availability", ["available_empty", "unavailable"])
def test_original_assessment_does_not_conflate_absent_and_unavailable_comments(case, availability):
    case.context = {"availability": availability, "records": []}

    result = _run(case)

    assert _receipt(result)["state"] == "sufficient"
    assert _receipt(result)["context_availability"] == availability
    assert all(call["context"] == case.context for call in case.generated)
    assert case.effects == []


@pytest.mark.parametrize("raw", [b"{malformed", b'{"reviews": []}', b"\xff"])
def test_generated_cache_is_excluded_without_read_quarantine_or_empty_context(case, raw):
    case.cache.write_bytes(raw)
    before = sorted(p.name for p in case.cache.parent.iterdir())

    result = _run(case)

    assert case.cache.exists() and case.cache.read_bytes() == raw
    assert sorted(p.name for p in case.cache.parent.iterdir()) == before
    assert case.effects == []
    assert _receipt(result)["state"] == "sufficient"
    assert _receipt(result)["context_availability"] == "available_nonempty"
    assert all(call["context"] == case.context for call in case.generated)


@pytest.mark.parametrize("mode", ["absent", "none", "disabled", "unavailable", "no_questions"])
def test_inactive_request_adds_zero_calls_and_preserves_ordinary_review(case, mode):
    questions = () if mode == "no_questions" else (QUESTION,)
    baseline = _run(case, request="absent", session_id="baseline", questions=questions)
    baseline_events = list(case.events)
    case.events.clear()
    if mode == "disabled":
        case.request.enabled = False
    if mode == "unavailable":
        case.request.available = False

    result = _run(case, request=mode if mode in {"absent", "none"} else "enabled", questions=questions)

    assert _baseline_projection(result) == _baseline_projection(baseline)
    assert case.events == baseline_events  # Existing ten fake acquisitions/reviews, zero EXTRA calls.
    assert case.generated == case.verified == case.effects == []
    assert all(not c.to_dict().get("question_evidence") for c in result.candidates)


def test_inactive_request_without_legacy_review_has_zero_acquisition_or_provider_calls(case):
    case.request.enabled = False

    _run(case, limits=(0, 0))

    assert not any(stage in {"snapshot", "context", "generate", "verify", "legacy_acquire"}
                   for stage, key in case.events)
    assert case.generated == case.verified == case.effects == []


def test_receipt_save_preserves_a_concurrent_explicit_add_key(case):
    def add_key(candidate_id):
        if candidate_id == "P11":
            session.update("shadow", lambda stored: setattr(stored.candidates[-1], "materialized_zotero_key", "ADDED011"))

    case.on_generate = add_key

    result = _run(case)

    assert _receipt(result)["state"] == "sufficient"
    assert result.candidates[-1].materialized_zotero_key == "ADDED011"
    assert case.effects == []
