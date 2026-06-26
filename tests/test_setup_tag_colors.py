"""goldenset setup-tag-colors — deterministic, non-destructive plan for native
keypress labeling in Zotero."""

from __future__ import annotations

import argparse
import json

from zotero_summarizer.cli._goldenset_setup_colors import (
    _goldenset_setup_tag_colors,
    _plan_rows,
)


def test_plan_is_the_four_labels_keyed_1_to_4_in_priority_order():
    rows = _plan_rows()
    assert [r["key"] for r in rows] == [1, 2, 3, 4]
    assert [r["tag"] for r in rows] == [
        "label:must_read",
        "label:should_read",
        "label:could_read",
        "label:dont_read",
    ]
    assert all(r["color"].startswith("#") and len(r["color"]) == 7 for r in rows)


def test_text_output_lists_each_label_tag_and_writes_nothing(capsys):
    rc = _goldenset_setup_tag_colors(argparse.Namespace(json=False))
    assert rc == 0
    out = capsys.readouterr().out
    for priority in ("must_read", "should_read", "could_read", "dont_read"):
        assert f"label:{priority}" in out
    assert "press 1/2/3/4" in out


def test_json_output_is_machine_readable(capsys):
    rc = _goldenset_setup_tag_colors(argparse.Namespace(json=True))
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert [r["key"] for r in payload["tag_colors"]] == [1, 2, 3, 4]
