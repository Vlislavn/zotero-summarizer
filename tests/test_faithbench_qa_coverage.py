"""QA-builder source coverage uses deterministic character-level oracles."""
import json
import math

import pytest

from zotero_summarizer.services.faithbench._build_qa import (
    _windows,
    build_items,
    generate_candidates,
)
from zotero_summarizer.services.faithbench._constants import QA_WINDOW_CHARS
from zotero_summarizer.services.faithbench._corpus import PaperRecord, sha256_text


@pytest.mark.parametrize("size", [0, 1, 5_999, 6_000, 6_001, 12_000, 12_001, 18_001, 24_001, 60_001])
def test_qa_windows_partition_every_source_character(size):
    text = "".join(chr(0x1000 + offset) for offset in range(size))

    windows = list(_windows(text))

    assert len(windows) == math.ceil(size / QA_WINDOW_CHARS)
    assert all(0 < len(window) <= QA_WINDOW_CHARS for window in windows)
    assert "".join(windows) == text


@pytest.mark.parametrize(
    ("size", "offset", "marker"),
    [
        (18_001, 12_000, "ZXQ18001"),
        (24_001, 16_500, "ZXQ24001"),
        (60_001, 42_003, "ZXQ60001"),
    ],
)
def test_generate_candidates_visits_interior_machine_sentinel(size, offset, marker):
    text = "x" * offset + marker + "x" * (size - offset - len(marker))

    class SentinelBuilder:
        def __init__(self):
            self.prompts = []

        def prompt(self, prompt, **kwargs):
            self.prompts.append(prompt)
            items = [{
                "question": "Which opaque marker occurs in the source?",
                "answer_span": marker,
                "answer_type": "entity",
            }] if marker in prompt else []
            return json.dumps({"items": items})

    builder = SentinelBuilder()
    candidates = generate_candidates(builder, title="T", text=text, per_window=1)

    assert any(marker in prompt for prompt in builder.prompts)
    assert [candidate["answer_span"] for candidate in candidates] == [marker]
    assert len(builder.prompts) == math.ceil(size / QA_WINDOW_CHARS)


def test_build_items_keeps_verified_unique_qa_and_constructs_cross_paper_traps():
    markers = {"A": "ZXA9001", "B": "ZXB9001"}
    papers = []
    for key, marker in markers.items():
        text = "x" * 12_000 + marker + "x" * (18_001 - 12_000 - len(marker))
        papers.append(PaperRecord(key, f"Paper {key}", text, sha256_text(text)))

    class GateBuilder:
        def __init__(self):
            self.prompts = []

        def prompt(self, prompt, **kwargs):
            self.prompts.append(prompt)
            marker = next((value for value in markers.values() if value in prompt), None)
            if marker is None:
                return json.dumps({"items": []})
            question = "Which opaque marker occurs in the source?"
            return json.dumps({"items": [
                {"question": question, "answer_span": marker, "answer_type": "entity"},
                {"question": question, "answer_span": marker, "answer_type": "entity"},
                {"question": "Which absent token is reported?", "answer_span": "NOTPRESENT"},
                {"question": "What source marker is independently recorded?", "answer_span": marker},
            ]})

    builder = GateBuilder()
    items = build_items(
        papers=papers,
        builder_llm=builder,
        qa_per_paper=2,
        traps_per_paper=1,
    )
    qa_items = [item for item in items if item.kind == "qa"]
    traps = [item for item in items if item.kind == "trap"]
    paper_by_key = {paper.item_key: paper for paper in papers}

    assert len(builder.prompts) == sum(
        math.ceil(len(paper.text) / QA_WINDOW_CHARS) for paper in papers
    )
    assert len(qa_items) == 2 * len(papers)
    assert len({(item.paper_item_key, item.question) for item in qa_items}) == len(qa_items)
    assert all(item.gold_answer != "NOTPRESENT" for item in qa_items)
    assert all(
        paper_by_key[item.paper_item_key].text[item.span_start:item.span_end] == item.gold_answer
        for item in qa_items
    )
    assert {item.paper_item_key: item.source_paper_item_key for item in traps} == {
        "A": "B",
        "B": "A",
    }
    assert len(traps) == 2


def test_input_over_configured_window_budget_fails_before_builder_call():
    class RecordingBuilder:
        def __init__(self):
            self.prompts = []

        def prompt(self, prompt, **kwargs):
            self.prompts.append(prompt)
            return '{"items": []}'

    builder = RecordingBuilder()

    with pytest.raises(ValueError, match="configured QA builder work limit"):
        generate_candidates(
            builder, title="T", text="x" * 12_001, per_window=1, max_builder_windows=2
        )

    assert builder.prompts == []


def test_build_preflights_every_paper_before_any_builder_call():
    class RecordingBuilder:
        def __init__(self):
            self.prompts = []

        def prompt(self, prompt, **kwargs):
            self.prompts.append(prompt)
            return '{"items": []}'

    papers = [
        PaperRecord("A", "Paper A", "x" * 100, sha256_text("x" * 100)),
        PaperRecord("B", "Paper B", "x" * 12_001, sha256_text("x" * 12_001)),
    ]
    builder = RecordingBuilder()

    with pytest.raises(ValueError, match="configured QA builder work limit"):
        build_items(
            papers=papers, builder_llm=builder, qa_per_paper=1, traps_per_paper=1,
            max_builder_windows=2,
        )

    assert builder.prompts == []


def test_explicit_work_limit_allows_complete_coverage_above_default():
    class RecordingBuilder:
        def __init__(self):
            self.prompts = []

        def prompt(self, prompt, **kwargs):
            self.prompts.append(prompt)
            return '{"items": []}'

    builder = RecordingBuilder()
    text = "x" * 192_001

    assert "".join(_windows(text, max_builder_windows=33)) == text
    assert generate_candidates(
        builder, title="T", text=text, per_window=1, max_builder_windows=33
    ) == []
    assert len(builder.prompts) == 33


def test_environment_work_limit_is_resolved_before_builder_call(monkeypatch):
    monkeypatch.setenv("ZS_FAITHBENCH_QA_MAX_BUILDER_WINDOWS", "2")

    class RecordingBuilder:
        def __init__(self):
            self.prompts = []

        def prompt(self, prompt, **kwargs):
            self.prompts.append(prompt)
            return '{"items": []}'

    builder = RecordingBuilder()

    with pytest.raises(ValueError, match="configured QA builder work limit"):
        generate_candidates(builder, title="T", text="x" * 12_001, per_window=1)

    assert builder.prompts == []


@pytest.mark.parametrize("limit", [0, -1, 257, True, 1.5])
def test_invalid_explicit_work_limit_fails_before_builder_call(limit):
    class RecordingBuilder:
        def __init__(self):
            self.prompts = []

        def prompt(self, prompt, **kwargs):
            self.prompts.append(prompt)
            return '{"items": []}'

    builder = RecordingBuilder()

    with pytest.raises(ValueError):
        generate_candidates(
            builder, title="T", text="x", per_window=1, max_builder_windows=limit
        )

    assert builder.prompts == []


def test_empty_source_needs_no_builder_call():
    class RecordingBuilder:
        def __init__(self):
            self.prompts = []

        def prompt(self, prompt, **kwargs):
            self.prompts.append(prompt)
            return '{"items": []}'

    builder = RecordingBuilder()

    assert list(_windows("")) == []
    assert generate_candidates(builder, title="T", text="", per_window=1) == []
    assert builder.prompts == []
