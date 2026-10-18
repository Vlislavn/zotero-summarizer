"""Prospective #7 machine-I/O regression; parent must observe preimage RED.

Copy unchanged to I/tests/test_qa_checkpoint_contract.py under the existing
conftest. No endpoint, persistence, semantic grading, or second QA harness.
Legacy missing/malformed input_digest policy is intentionally not specified.
"""
from __future__ import annotations

import json
from copy import deepcopy

import pytest
from pydantic import ValidationError

from zotero_summarizer.api.routes.library import AskPaperRequest
from zotero_summarizer.services.library import qa_context

ITEM_KEY = "P1"
PREFIX = "Earlier evidence checkpoint: "
WARNING = "(resolve against the current extraction before exact claims)"
HANDLE_KEYS = {"paper_id", "extraction_version", "span", "quote_digest", "input_digest"}
QUOTES = ("Exact supporting quote here.", "A second distinct source quote lives here.")


def _render(turns):
    """Independent spelling of the existing raw/recent wire-text contract."""
    rendered = []
    for turn in turns:
        question = str(turn.get("question") or "").strip()
        answer = str(turn.get("answer") or "[abstained]").strip()
        quote = str(turn.get("quote") or "").strip()
        rendered.append(f"User: {question}\nAssistant: {answer}" +
                        (f"\nPrior quote: {quote}" if quote else ""))
    return "\n".join(rendered)


def _request(history):
    return AskPaperRequest(item_key=ITEM_KEY, question="Current question?", history=history)


def _dto_turns(history):
    req = _request(history)
    return [turn.model_dump(mode="json") for turn in req.history]


def _decode_checkpoint(text, recent):
    first, separator, rest = text.partition("\n")
    assert separator and first.startswith(PREFIX), "checkpoint first-line framing"
    try:
        handles = json.loads(first[len(PREFIX):])
    except json.JSONDecodeError as exc:
        pytest.fail(f"checkpoint payload must be one standard JSON array: {exc}")
    assert isinstance(handles, list), "checkpoint payload must be an array"
    assert rest == WARNING + "\n" + _render(recent), "warning/recent wire-text changed"
    return handles


def _assert_checkpoint(result, artifact, recent, expected):
    assert isinstance(result, tuple) and len(result) == 3
    text, compacted, count = result
    assert isinstance(text, str) and isinstance(count, int)
    assert compacted is True and count == len(expected)
    recovered = _decode_checkpoint(text, recent)
    assert recovered == expected, "complete native identities, order and occurrences"
    for handle in recovered:
        assert set(handle) == HANDLE_KEYS
        assert set(handle["span"]) == {"start", "end"}
        assert len(handle["quote_digest"]) == len(handle["input_digest"]) == 64
        assert qa_context.verified_quote(ITEM_KEY, artifact, handle) is not None
    return text


def _reference_candidate(handles, recent):
    """Sizing precondition only; JSON whitespace is not a new wire policy."""
    return PREFIX + json.dumps(handles) + "\n" + WARNING + "\n" + _render(recent)


@pytest.fixture
def native_history():
    body = "Before. " + QUOTES[0] + " Between. " + QUOTES[1] + " After."
    artifact = {"pdf_key": "fixture-v1", "full_text": body}
    questions = ("  Original   question one? ", "Original question two?")
    handles = [
        qa_context.evidence_handle(ITEM_KEY, artifact, question, quote)
        for question, quote in zip(questions, QUOTES)
    ]
    for handle, quote in zip(handles, QUOTES):
        assert set(handle) == HANDLE_KEYS, "native factory setup"
        assert handle["paper_id"] == ITEM_KEY
        assert handle["extraction_version"] == "fixture-v1"
        start = body.index(quote)
        assert handle["span"] == {"start": start, "end": start + len(quote)}
        assert len(handle["quote_digest"]) == len(handle["input_digest"]) == 64
        assert qa_context.verified_quote(ITEM_KEY, artifact, handle) == quote
    assert handles[0]["span"] != handles[1]["span"]
    assert handles[0]["quote_digest"] != handles[1]["quote_digest"]
    assert handles[0]["input_digest"] != handles[1]["input_digest"]
    assert handles[0] == qa_context.evidence_handle(
        ITEM_KEY, artifact, "Original question one?", QUOTES[0],
    )
    older = [
        {"question": question, "answer": f"OLDER_{index}_" + "a" * 1800,
         "quote": quote, "evidence_handle": handle}
        for index, (question, quote, handle) in enumerate(zip(questions, QUOTES, handles))
    ]
    recent = [
        {"question": f" recent question {index} ",
         "answer": None if index == 0 else f" recent answer {index} ",
         "quote": f" prior quote {index} ", "evidence_handle": None}
        for index in range(4)
    ]
    dumped = _dto_turns(older + recent)
    assert [turn["evidence_handle"] for turn in dumped[:2]] == handles
    assert all(len(turn["answer"] or "") <= 5000 for turn in dumped)
    assert len(_reference_candidate(handles, dumped[-4:])) < len(_render(dumped))
    return artifact, dumped, handles


def test_compacted_checkpoint_retains_roundtrippable_source_handles(native_history):
    artifact, history, handles = native_history
    before = deepcopy(history)

    result = qa_context.compact_history(ITEM_KEY, artifact, history)

    text = _assert_checkpoint(result, artifact, before[-4:], handles)
    recovered = _decode_checkpoint(text, before[-4:])
    assert [qa_context.verified_quote(ITEM_KEY, artifact, handle)
            for handle in recovered] == list(QUOTES)
    assert len(text) < len(_render(before))
    assert "OLDER_0_" not in text and "OLDER_1_" not in text
    assert "Verified quote:" not in text
    assert qa_context.compact_history(ITEM_KEY, artifact, history) == result


def test_client_top_and_span_extras_do_not_enter_checkpoint(native_history):
    artifact, history, handles = native_history
    enriched = deepcopy(history)
    for turn in enriched[:2]:
        turn["evidence_handle"]["client_extra"] = "TOP_SENTINEL"
        turn["evidence_handle"]["span"]["client_extra"] = "SPAN_SENTINEL"
    dumped = _dto_turns(enriched)
    assert dumped == enriched, "DTO must retain extras before checkpoint projection"
    for turn, quote in zip(dumped[:2], QUOTES):
        assert qa_context.verified_quote(ITEM_KEY, artifact, turn["evidence_handle"]) == quote

    result = qa_context.compact_history(ITEM_KEY, artifact, dumped)

    text = _assert_checkpoint(result, artifact, dumped[-4:], handles)
    assert "TOP_SENTINEL" not in text and "SPAN_SENTINEL" not in text


def test_only_pdf_key_change_excludes_stale_handles(native_history):
    artifact, history, handles = native_history
    reparsed = {**artifact, "pdf_key": "fixture-v2"}
    assert reparsed["full_text"] == artifact["full_text"]
    assert all(qa_context.verified_quote(ITEM_KEY, reparsed, handle) is None
               for handle in handles)
    assert len(_reference_candidate([], history[-4:])) < len(_render(history))

    result = qa_context.compact_history(ITEM_KEY, reparsed, history)

    _assert_checkpoint(result, reparsed, history[-4:], [])


@pytest.mark.parametrize("count", [0, 1, 4])
def test_up_to_four_turns_stay_raw(native_history, count):
    artifact, history, _ = native_history
    short = history[-count:] if count else []

    result = qa_context.compact_history(ITEM_KEY, artifact, short)

    assert result == (_render(short), False, 0)


def test_more_than_four_short_turns_with_handles_stay_raw(native_history):
    artifact, _, handles = native_history
    short = [{"question": "q", "answer": "a", "quote": None,
              "evidence_handle": handles[index] if index < 2 else None}
             for index in range(6)]
    history = _dto_turns(short)
    assert len(history) > 4
    assert len(_reference_candidate(handles, history[-4:])) > len(_render(history))

    result = qa_context.compact_history(ITEM_KEY, artifact, history)

    assert result == (_render(history), False, 0)


@pytest.mark.parametrize("raw_minus_candidate", [-1, 0, 1],
                         ids=["candidate-larger", "equal-size", "strictly-smaller"])
def test_size_comparison_is_strict(native_history, raw_minus_candidate):
    artifact, history, handles = native_history
    # Validate the admitted candidate against the independent machine contract
    # before using its length. This tolerates standard JSON whitespace choices;
    # it never derives expected handles/framing from an unvalidated preimage.
    admitted = qa_context.compact_history(ITEM_KEY, artifact, history)
    candidate = _assert_checkpoint(admitted, artifact, history[-4:], handles)
    boundary = deepcopy(history)
    boundary[0]["answer"], boundary[1]["answer"] = "a", "b"
    padding = len(candidate) + raw_minus_candidate - len(_render(boundary))
    assert 0 <= padding <= 4999, "boundary fixture sizing setup"
    boundary[0]["answer"] += "p" * padding
    boundary = _dto_turns(boundary)
    raw = _render(boundary)
    assert len(raw) - len(candidate) == raw_minus_candidate
    assert [turn["evidence_handle"] for turn in boundary[:2]] == handles
    assert boundary[-4:] == history[-4:]

    result = qa_context.compact_history(ITEM_KEY, artifact, boundary)

    if raw_minus_candidate <= 0:
        assert result == (raw, False, 0)
    else:
        assert result == (candidate, True, 2)


def test_direct_service_uses_last_twenty_and_dto_accepts_twenty(native_history):
    artifact, history, handles = native_history
    excluded = [
        {"question": f"EXCLUDED_{index}", "answer": "excluded prose " * 100,
         "quote": quote, "evidence_handle": qa_context.evidence_handle(
             ITEM_KEY, artifact, f"EXCLUDED_{index}", quote)}
        for index, quote in enumerate(QUOTES)
    ]
    bounded = _dto_turns(deepcopy(history[:2]) * 8 + history[-4:])
    direct = excluded + bounded
    assert len(bounded) == 20 and len(direct) == 22
    expected = handles * 8
    recent, raw = deepcopy(bounded[-4:]), _render(bounded)
    assert len(_reference_candidate(expected, recent)) < len(raw)

    direct_result = qa_context.compact_history(ITEM_KEY, artifact, direct)
    dto_result = qa_context.compact_history(ITEM_KEY, artifact, bounded)

    text = _assert_checkpoint(direct_result, artifact, recent, expected)
    assert dto_result == direct_result
    assert len(text) < len(raw)
    assert "EXCLUDED_" not in text and "excluded prose" not in text
    assert all(turn["evidence_handle"]["input_digest"] not in text for turn in excluded)


def test_dto_rejects_more_than_twenty_turns(native_history):
    _, history, _ = native_history
    twenty = deepcopy(history[:2]) * 8 + history[-4:]
    assert len(_request(twenty).history) == 20

    with pytest.raises(ValidationError) as caught:
        _request([history[0], *twenty])

    assert any(error["loc"] == ("history",) and error["type"] == "too_long"
               for error in caught.value.errors())


def test_repeated_handle_occurrences_are_not_deduplicated(native_history):
    artifact, history, handles = native_history
    repeated = _dto_turns([history[0], history[0], history[1], history[0], *history[-4:]])
    expected = [handles[0], handles[0], handles[1], handles[0]]

    result = qa_context.compact_history(ITEM_KEY, artifact, repeated)

    text = _assert_checkpoint(result, artifact, repeated[-4:], expected)
    assert len(text) < len(_render(repeated))


@pytest.mark.parametrize("invalid_handle", [False, True], ids=["no-handle", "wrong-paper"])
@pytest.mark.parametrize("long_answers", [False, True], ids=["short-raw", "long-checkpoint"])
def test_no_current_handles_preserves_size_fallback(native_history, invalid_handle, long_answers):
    artifact, _, handles = native_history
    rejected = {**handles[0], "paper_id": "OTHER"} if invalid_handle else None
    assert qa_context.verified_quote(ITEM_KEY, artifact, rejected) is None
    history = _dto_turns([
        {"question": "q", "answer": "a" * (1800 if long_answers else 1),
         "quote": None, "evidence_handle": rejected}
        for _ in range(6)
    ])
    raw = _render(history)
    reference = _reference_candidate([], history[-4:])
    assert (len(reference) < len(raw)) is long_answers

    result = qa_context.compact_history(ITEM_KEY, artifact, history)

    if long_answers:
        text = _assert_checkpoint(result, artifact, history[-4:], [])
        assert len(text) < len(raw)
    else:
        assert result == (raw, False, 0)
