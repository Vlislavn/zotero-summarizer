"""Golden read-modify-write operations cannot lose acknowledged label rows."""
import csv
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from multiprocessing import get_context
from threading import Barrier
from types import SimpleNamespace

import pytest

from test_goldenset_export_preserves_ui_rows import _sample

from zotero_summarizer.services.golden.goldenset import _write_csv
from zotero_summarizer.services.library.review_summary import _write_golden_sample
from zotero_summarizer.services.model import classifier_io, llm_classifier


def test_concurrent_unique_appends_preserve_every_row(tmp_path):
    path = tmp_path / "golden.csv"
    _write_csv([_sample("SEED")], path)
    barrier = Barrier(40)

    def append(index):
        barrier.wait(timeout=10)
        return _write_golden_sample(_sample(f"feed:{index}"), path)

    with ThreadPoolExecutor(max_workers=40) as pool:
        assert all(pool.map(append, range(40)))
    with path.open(newline="", encoding="utf-8") as f:
        keys = [row["item_key"] for row in csv.DictReader(f)]
    assert len(keys) == 41
    assert set(keys) == {"SEED", *(f"feed:{index}" for index in range(40))}


def _append_in_process(args):
    path, index = args
    return _write_golden_sample(_sample(f"feed:{index}"), path)


def test_process_writers_and_duplicate_appends(tmp_path):
    path = tmp_path / "golden.csv"
    _write_csv([_sample("SEED")], path)
    with ProcessPoolExecutor(max_workers=4, mp_context=get_context("spawn")) as pool:
        results = list(pool.map(_append_in_process, [(path, i % 10) for i in range(40)]))
    assert sum(results) == 10
    with path.open(newline="", encoding="utf-8") as f:
        assert len(list(csv.DictReader(f))) == 11
    before = path.stat().st_mtime_ns
    assert not _write_golden_sample(_sample("feed:0"), path)
    assert path.stat().st_mtime_ns == before


def test_prediction_writers_and_appends_share_the_lock(tmp_path):
    path = tmp_path / "golden.csv"
    _write_csv([_sample("SEED")], path)
    report = SimpleNamespace(
        item_keys=["SEED"], cv_probabilities=[4.0], cv_predictions=["should_read"],
        holdout_item_keys=[], holdout_probabilities=[], holdout_predictions=[],
    )
    classifications = [llm_classifier.LLMClassification("SEED", "must_read", 0.9, "reason")]
    barrier = Barrier(20)

    def write(index):
        barrier.wait(timeout=10)
        if index % 2:
            classifier_io.write_predictions_to_csv(path, report, classifier_name=f"ml{index}")
        else:
            llm_classifier.write_predictions_to_csv(path, classifications, classifier_name=f"llm{index}")
        return _write_golden_sample(_sample(f"feed:{index}"), path)

    with ThreadPoolExecutor(max_workers=20) as pool:
        assert all(pool.map(write, range(20)))
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 21
    for index in range(20):
        name = f"ml{index}" if index % 2 else f"llm{index}"
        assert rows[0][f"cls_{name}_priority"] == ("should_read" if index % 2 else "must_read")


def test_export_and_appends_preserve_all_namespaced_labels(tmp_path):
    path = tmp_path / "golden.csv"
    _write_csv([_sample("SEED")], path)
    barrier = Barrier(20)

    def write(index):
        barrier.wait(timeout=10)
        _write_golden_sample(_sample(f"feed:{index}"), path)
        _write_csv([_sample("SEED")], path)

    with ThreadPoolExecutor(max_workers=20) as pool:
        list(pool.map(write, range(20)))
    with path.open(newline="", encoding="utf-8") as f:
        assert len(list(csv.DictReader(f))) == 21


def test_failed_publication_preserves_original_and_releases_lock(tmp_path, monkeypatch):
    from zotero_summarizer.services import _common

    path = tmp_path / "golden.csv"
    _write_csv([_sample("SEED")], path)
    before = path.read_bytes()

    def fail_replace(*args):
        raise OSError("publication failed")

    with monkeypatch.context() as scoped:
        scoped.setattr(_common.os, "replace", fail_replace)
        with pytest.raises(OSError, match="publication failed"):
            _write_golden_sample(_sample("feed:1"), path)
    assert path.read_bytes() == before
    assert list(tmp_path.glob("*.tmp")) == []
    assert _write_golden_sample(_sample("feed:1"), path)


@pytest.mark.parametrize("content", ["bad\nvalue\n", "item_key,title\nA\n", "item_key,item_key\nA,B\n"])
def test_malformed_csv_is_not_rewritten(tmp_path, content):
    path = tmp_path / "golden.csv"
    path.write_text(content)
    with pytest.raises(ValueError):
        _write_golden_sample(_sample("feed:1"), path)
    assert path.read_text() == content
