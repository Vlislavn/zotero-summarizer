"""``zotero-summarizer similar``: output format, limit and failure exits (service calls replaced)."""
from __future__ import annotations

import json

import pytest

from zotero_summarizer.cli import _similar, build_parser

ITEMS = [
    {"item_key": "AAAA1111", "title": "Debate  among\nagents", "year": "2026", "authors": "Ann Lee; Bo Wu",
     "read": False, "why_reason": "strong fit " * 30},
    {"item_key": "BBBB2222", "title": "Old judge paper", "year": "", "authors": ["Cy Zed"], "read": True,
     "why_reason": None},
    {"item_key": "CCCC3333", "title": "Third", "year": "2024", "authors": "", "read": False},
]


def _run(monkeypatch, result, argv):
    monkeypatch.setattr(_similar, "_bootstrap", lambda: None)
    calls = []
    monkeypatch.setattr(_similar, "_search", lambda query, limit: calls.append((query, limit)) or result)
    args = build_parser().parse_args(["similar", *argv])
    return _similar._run(args), calls


def test_prints_flags_line_then_one_line_per_paper(monkeypatch, capsys):
    result = {"semantic": True, "reranked": True, "items": ITEMS, "stale_since": "2026-11-25T06:44:04+00:00"}
    code, calls = _run(monkeypatch, result, ["multi-agent debate", "--limit", "3"])
    lines = capsys.readouterr().out.splitlines()

    assert code == 0 and calls == [("multi-agent debate", 3)]
    assert lines[0] == "semantic=yes reranked=yes zotero_copy=stale since 2026-11-25T06:44:04+00:00"
    first = lines[1].split(" · ")
    assert first[:5] == ["1", "Debate among agents", "2026", "Ann Lee", "unread"]
    assert len(first[5]) <= 160 and first[6] == "zotero://select/library/items/AAAA1111"
    assert lines[2] == "2 · Old judge paper · - · Cy Zed · read · - · zotero://select/library/items/BBBB2222"
    assert len(lines) == 4


def test_default_limit_is_ten_and_loading_reranker_is_flagged(monkeypatch, capsys):
    result = {"semantic": True, "reranked": False, "reranker_loading": True, "items": ITEMS[:1]}
    code, calls = _run(monkeypatch, result, ["debate"])

    assert code == 0 and calls == [("debate", 10)]
    assert capsys.readouterr().out.splitlines()[0] == (
        "semantic=yes reranked=no (reranker still loading) zotero_copy=fresh")


def test_json_prints_the_items(monkeypatch, capsys):
    code, _ = _run(monkeypatch, {"semantic": True, "items": ITEMS}, ["debate", "--json"])

    assert code == 0 and json.loads(capsys.readouterr().out) == ITEMS


@pytest.mark.parametrize("result", [{"semantic": False, "items": ITEMS}, {"semantic": True, "items": []}])
def test_no_semantic_results_exit_nonzero_with_a_message(monkeypatch, capsys, result):
    code, _ = _run(monkeypatch, result, ["debate"])
    captured = capsys.readouterr()

    assert code == 1 and captured.out == "" and "no meaning-search results" in captured.err


def test_runtime_failure_exits_nonzero_with_one_line(monkeypatch, capsys):
    def boom():
        raise RuntimeError("the corpus is off")

    monkeypatch.setattr(_similar, "_bootstrap", boom)
    code = _similar._run(build_parser().parse_args(["similar", "debate"]))

    assert code == 1 and "RuntimeError: the corpus is off" in capsys.readouterr().err


def test_command_ends_the_process_with_the_exit_code(monkeypatch):
    exits = []
    monkeypatch.setattr(_similar, "_run", lambda args: 7)
    monkeypatch.setattr(_similar.os, "_exit", exits.append)

    _similar._command(build_parser().parse_args(["similar", "debate"]))

    assert exits == [7]
