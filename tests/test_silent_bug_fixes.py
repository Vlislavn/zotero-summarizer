"""Regression guards for the silent-bug fixes (deep-review batch)."""
from __future__ import annotations

import csv
import tempfile
from dataclasses import fields
from pathlib import Path

import pytest


# --- #1 derivation thresholds == prediction thresholds ---------------------
def test_priority_derivation_matches_prediction_bins():
    from zotero_summarizer.domain import score_to_priority
    from zotero_summarizer.services.emoji_signals import priority_for_score

    for s in (1.0, 1.99, 2.0, 2.3, 2.5, 3.0, 3.49, 3.5, 3.55, 4.0, 4.49, 4.5, 5.0):
        assert priority_for_score(s) == score_to_priority(s), f"bin disagreement at {s}"


# --- #2 one relevance mapping, round-tripping through score_to_priority -----
def test_priority_to_relevance_is_single_source_and_round_trips():
    from zotero_summarizer.domain import PRIORITY_TO_RELEVANCE, score_to_priority
    from zotero_summarizer.services.golden import goldenset, hybrid_gt
    from zotero_summarizer.services.golden.relabel_audit import _constants as ac
    from zotero_summarizer.services.triage.feeds import _gate

    assert goldenset._PRIORITY_TO_RELEVANCE is PRIORITY_TO_RELEVANCE
    assert hybrid_gt._PRIORITY_TO_RELEVANCE is PRIORITY_TO_RELEVANCE
    assert dict(ac.PRIORITY_TO_SCORE) == dict(PRIORITY_TO_RELEVANCE)
    assert _gate._PRIORITY_TO_RELEVANCE is PRIORITY_TO_RELEVANCE
    # should_read must NOT sit on the must_read boundary.
    assert PRIORITY_TO_RELEVANCE["should_read"] < 4.5
    for cls, value in PRIORITY_TO_RELEVANCE.items():
        assert score_to_priority(value) == cls


# --- #3 DOI normalization catches URL/prefix variants ----------------------
def test_normalize_doi_collapses_variants():
    from zotero_summarizer.domain import normalize_doi

    bare = "10.1234/abc"
    assert normalize_doi("https://doi.org/10.1234/ABC") == bare
    assert normalize_doi("doi:10.1234/abc") == bare
    assert normalize_doi("10.1234/abc/") == bare
    assert normalize_doi("  https://dx.doi.org/10.1234/abc  ") == bare
    assert normalize_doi("") == ""


# --- #6 clamp surfaces NaN instead of silently returning the high bound -----
def test_clamp_raises_on_nan():
    from zotero_summarizer.services._common import clamp

    assert clamp(7.0, 1.0, 5.0) == 5.0  # normal clamp still works
    with pytest.raises(ValueError):
        clamp(float("nan"), 1.0, 5.0)


# --- #4 atomic_write leaves the original intact when the write fails --------
def test_atomic_write_preserves_original_on_failure():
    from zotero_summarizer.services._common import atomic_write

    d = Path(tempfile.mkdtemp())
    target = d / "data.txt"
    target.write_text("ORIGINAL")

    def boom(_tmp: Path) -> None:
        raise OSError("simulated mid-write crash")

    with pytest.raises(OSError):
        atomic_write(target, boom)
    assert target.read_text() == "ORIGINAL"  # untouched


def test_golden_csv_write_survives_crash(monkeypatch):
    from zotero_summarizer.services.golden import goldenset as gs

    def mk(item_key: str) -> "gs.GoldenSample":
        kw = {}
        for f in fields(gs.GoldenSample):
            if f.name.endswith("_count") or f.name == "days_since_added":
                kw[f.name] = 0
            elif f.name == "in_trash":
                kw[f.name] = False
            elif f.name == "gold_inferred_relevance":
                kw[f.name] = 3.0
            else:
                kw[f.name] = item_key if f.name == "item_key" else ""
        return gs.GoldenSample(**kw)

    path = Path(tempfile.mkdtemp()) / "golden.csv"
    gs._write_csv([mk("feed:42")], path)
    assert "feed:42" in path.read_text()

    calls = {"n": 0}
    orig = csv.DictWriter.writerow

    def boom(self, row):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("crash")
        return orig(self, row)

    monkeypatch.setattr(csv.DictWriter, "writerow", boom)
    with pytest.raises(OSError):
        gs._write_csv([mk("ZOT1")], path)
    monkeypatch.undo()
    assert "feed:42" in path.read_text()  # preserved row not lost


# --- #7 adaptive cutoffs keep a real should_read band on a tiny fold --------
def test_adaptive_cutoffs_stable_on_tiny_keep_group():
    import numpy as np
    from zotero_summarizer.services.model.classifier_fit import _adaptive_4class_cutoffs

    must_t, could_t = _adaptive_4class_cutoffs(np.array([0.05, 0.07, 0.10, 0.90]), 0.5)
    assert must_t > 0.5, "must_t must stay above the keep cutoff"
    assert could_t < 0.5, "could_t must stay below the keep cutoff"
    assert must_t - 0.5 > 0.0, "should_read band must be non-empty (was collapsed)"
