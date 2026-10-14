"""Prospective option-A legacy controls; no execution or production correction.

Requires the immutable core incorporated as tests/test_qa_checkpoint_contract.py
and importable as test_qa_checkpoint_contract under the existing pytest setup.
Imported native_history registers that existing fixture in this module.
Do not reuse core _assert_checkpoint for opaque/null legacy input_digest values.
"""
from __future__ import annotations

from copy import deepcopy

import pytest

from zotero_summarizer.services.library import qa_context
from test_qa_checkpoint_contract import (
    HANDLE_KEYS,
    ITEM_KEY,
    QUOTES,
    _decode_checkpoint,
    _dto_turns,
    _reference_candidate,
    _render,
    native_history,
)

_ABSENT = object()


def _native_input_digest(artifact, question, quote=QUOTES[0]):
    """Reuse the producer for expected QUESTION digest only, never source span."""
    produced = qa_context.evidence_handle(ITEM_KEY, artifact, question, quote)
    assert isinstance(produced, dict), "native digest oracle setup"
    return produced["input_digest"]


def _fixture_preconditions(artifact, history, expected):
    assert 4 < len(history) <= 20
    assert len(history[:-4]) == len(expected)
    assert all(len(turn["answer"] or "") <= 5000 for turn in history)
    for turn, handle in zip(history[:-4], expected):
        assert qa_context.verified_quote(
            ITEM_KEY, artifact, turn["evidence_handle"],
        ) == turn["quote"], "legacy source admission setup"
        assert qa_context.verified_quote(ITEM_KEY, artifact, handle) == turn["quote"]
    assert len(_reference_candidate(expected, history[-4:])) < len(_render(history)), (
        "complete legacy checkpoint must genuinely fit before compaction"
    )


def _assert_legacy_checkpoint(result, artifact, before, expected):
    assert isinstance(result, tuple) and len(result) == 3
    text, compacted, count = result
    assert isinstance(text, str) and isinstance(count, int)
    assert compacted is True and count == len(expected)
    recovered = _decode_checkpoint(text, before[-4:])
    assert recovered == expected, "legacy retain/derive/unknown projection"
    for handle, turn in zip(recovered, before[:-4]):
        assert set(handle) == HANDLE_KEYS
        assert set(handle["span"]) == {"start", "end"}
        assert qa_context.verified_quote(ITEM_KEY, artifact, handle) == turn["quote"]
    assert len(text) < len(_render(before))
    return recovered


def test_supplied_native_digest_survives_different_history_question(native_history):
    artifact, history, handles = native_history
    changed = deepcopy(history)
    changed[0]["question"] = "A different history question?"
    recomputed = _native_input_digest(artifact, changed[0]["question"])
    assert recomputed != handles[0]["input_digest"], "preserve vs recompute discriminator"
    dumped = _dto_turns(changed)
    assert dumped[0]["evidence_handle"] == handles[0]
    expected, before = deepcopy(handles), deepcopy(dumped)
    _fixture_preconditions(artifact, dumped, expected)

    result = qa_context.compact_history(ITEM_KEY, artifact, dumped)

    recovered = _assert_legacy_checkpoint(result, artifact, before, expected)
    assert recovered[0]["input_digest"] == handles[0]["input_digest"]
    assert recovered[0]["input_digest"] != recomputed


@pytest.mark.parametrize("question, normalized", [
    ("Legacy question about source?", "Legacy question about source?"),
    ("  Legacy\tquestion \n about source? ", "Legacy question about source?"),
    (" \t\n ", ""),
], ids=["plain", "normalized-whitespace", "whitespace-only"])
def test_missing_input_uses_real_dto_question_digest(native_history, question, normalized):
    artifact, history, handles = native_history
    changed = deepcopy(history)
    changed[0]["question"] = question
    changed[0]["answer"] = "DIFFERENT_OLDER_ANSWER_" + "b" * 1900
    del changed[0]["evidence_handle"]["input_digest"]
    dumped = _dto_turns(changed)
    assert dumped[0]["question"] == question, "actual DTO question must survive"
    assert "input_digest" not in dumped[0]["evidence_handle"]
    digest = _native_input_digest(artifact, question)
    # A different quote must not enter the question-only digest oracle.
    assert digest == _native_input_digest(artifact, normalized, QUOTES[1])
    expected = deepcopy(handles)
    expected[0]["input_digest"] = digest
    before = deepcopy(dumped)
    _fixture_preconditions(artifact, dumped, expected)

    result = qa_context.compact_history(ITEM_KEY, artifact, dumped)

    _assert_legacy_checkpoint(result, artifact, before, expected)


@pytest.mark.parametrize("malformed", [
    None,
    True,
    False,
    ["INPUT_LIST_SENTINEL", {"nested": "NESTED_LIST_SENTINEL"}],
    {"client_extra": {"nested": "INPUT_DICT_SENTINEL"}},
], ids=["null", "true", "false", "list", "dict"])
def test_nonstring_input_derives_and_client_extras_never_leak(native_history, malformed):
    artifact, history, handles = native_history
    changed = deepcopy(history)
    changed[0]["question"] = "  Derive\tthis legacy question? "
    handle = changed[0]["evidence_handle"]
    handle["input_digest"] = deepcopy(malformed)
    handle["client_extra"] = {"nested": "TOP_SENTINEL"}
    handle["span"]["client_extra"] = {"nested": "SPAN_SENTINEL"}
    dumped = _dto_turns(changed)
    assert dumped == changed, "DTO must retain all sentinels before projection"
    expected = deepcopy(handles)
    expected[0]["input_digest"] = _native_input_digest(artifact, dumped[0]["question"])
    before = deepcopy(dumped)
    _fixture_preconditions(artifact, dumped, expected)

    result = qa_context.compact_history(ITEM_KEY, artifact, dumped)

    _assert_legacy_checkpoint(result, artifact, before, expected)
    assert all(sentinel not in result[0] for sentinel in (
        "TOP_SENTINEL", "SPAN_SENTINEL", "INPUT_LIST_SENTINEL",
        "NESTED_LIST_SENTINEL", "INPUT_DICT_SENTINEL",
    ))


@pytest.mark.parametrize("opaque", ["  opaque <legacy '&value'>\n ", ""],
                         ids=["opaque-string", "empty-string"])
def test_supplied_opaque_or_empty_string_is_preserved(native_history, opaque):
    artifact, history, handles = native_history
    changed = deepcopy(history)
    changed[0]["evidence_handle"]["input_digest"] = opaque
    dumped = _dto_turns(changed)
    assert dumped[0]["evidence_handle"]["input_digest"] == opaque
    assert opaque != _native_input_digest(artifact, dumped[0]["question"])
    expected = deepcopy(handles)
    expected[0]["input_digest"] = opaque
    before = deepcopy(dumped)
    _fixture_preconditions(artifact, dumped, expected)

    result = qa_context.compact_history(ITEM_KEY, artifact, dumped)

    recovered = _assert_legacy_checkpoint(result, artifact, before, expected)
    assert recovered[0]["input_digest"] == opaque


@pytest.mark.parametrize("question, input_value", [
    (_ABSENT, _ABSENT),
    (None, None),
    (True, True),
    (False, {"client_extra": "UNKNOWN_INPUT_SENTINEL"}),
    (7, _ABSENT),
    (["QUESTION_LIST_SENTINEL"], ["UNKNOWN_LIST_INPUT_SENTINEL"]),
    ({"q": "QUESTION_DICT_SENTINEL"}, {"client_extra": "UNKNOWN_DICT_INPUT_SENTINEL"}),
], ids=["absent", "null", "true", "false", "number", "list", "dict"])
def test_direct_missing_or_nonstring_question_keeps_unknown_input(
    native_history, question, input_value,
):
    artifact, history, handles = native_history
    direct = deepcopy(history)
    if question is _ABSENT:
        del direct[0]["question"]
    else:
        direct[0]["question"] = deepcopy(question)
    if input_value is _ABSENT:
        del direct[0]["evidence_handle"]["input_digest"]
    else:
        direct[0]["evidence_handle"]["input_digest"] = deepcopy(input_value)
    expected = deepcopy(handles)
    expected[0]["input_digest"] = None
    before = deepcopy(direct)
    # Deliberately no DTO bypass: these are direct service calls, not HTTP turns.
    _fixture_preconditions(artifact, direct, expected)

    result = qa_context.compact_history(ITEM_KEY, artifact, direct)

    recovered = _assert_legacy_checkpoint(result, artifact, before, expected)
    assert recovered[0]["input_digest"] is None
    assert all(sentinel not in result[0] for sentinel in (
        "QUESTION_LIST_SENTINEL", "QUESTION_DICT_SENTINEL", "UNKNOWN_INPUT_SENTINEL",
        "UNKNOWN_LIST_INPUT_SENTINEL", "UNKNOWN_DICT_INPUT_SENTINEL",
    ))


@pytest.mark.parametrize("string_coordinates", [False, True],
                         ids=["integer-span", "accepted-string-span"])
def test_second_quote_span_and_duplicate_older_occurrences_survive_derivation(
    native_history, string_coordinates,
):
    artifact, history, handles = native_history
    repeated_artifact = {**artifact, "full_text": artifact["full_text"] + " Extra. " + QUOTES[0]}
    body = repeated_artifact["full_text"]
    original = deepcopy(handles[0])
    second_start = body.rindex(QUOTES[0])
    assert second_start != body.index(QUOTES[0]), "two distinct exact occurrences"
    start_value = str(second_start) if string_coordinates else second_start
    second_end = second_start + len(QUOTES[0])
    end_value = str(second_end) if string_coordinates else second_end
    original["span"] = {"start": start_value, "end": end_value}
    del original["input_digest"]
    assert qa_context.verified_quote(ITEM_KEY, repeated_artifact, original) == QUOTES[0]
    older = deepcopy(history[0])
    older["question"] = "  Question\tfor the second occurrence? "
    older["evidence_handle"] = original
    dumped = _dto_turns([older, history[1], deepcopy(older), *history[-4:]])
    expected_second = deepcopy(original)
    expected_second["input_digest"] = _native_input_digest(repeated_artifact, older["question"])
    expected = [expected_second, deepcopy(handles[1]), deepcopy(expected_second)]
    before = deepcopy(dumped)
    _fixture_preconditions(repeated_artifact, dumped, expected)

    result = qa_context.compact_history(ITEM_KEY, repeated_artifact, dumped)

    recovered = _assert_legacy_checkpoint(result, repeated_artifact, before, expected)
    assert recovered[0] == recovered[2] and result[2] == 3
    assert recovered[0]["span"] == original["span"] == recovered[2]["span"]
    assert recovered[0]["span"]["start"] == start_value
    assert recovered[0]["span"]["end"] == end_value
    assert qa_context.compact_history(ITEM_KEY, repeated_artifact, dumped) == result
