"""Model publication is one atomic artifact, including metadata."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Barrier
from types import SimpleNamespace

import pytest
import joblib
from zipfile import BadZipFile, ZipFile

from zotero_summarizer.services._common import atomic_write
from zotero_summarizer.services.model.classifier_training import save_trained
from zotero_summarizer.services.model import classifier_store as store
from zotero_summarizer.services.model.classifier_backup import restore_snapshot, snapshot_current


def _model(version):
    return SimpleNamespace(
        classifier_name="logreg", golden_csv_sha256=version, feature_dim=1,
        pca_dim=0, t_keep=0.4, t_must=0.7, t_could=0.5,
        training_metadata={"version": version},
    )


def test_metadata_failure_leaves_live_model_unchanged(tmp_path):
    path = save_trained(_model("old"), tmp_path)
    before = path.read_bytes()
    invalid = _model("new")
    invalid.training_metadata["unserializable"] = object()
    with pytest.raises(TypeError):
        save_trained(invalid, tmp_path)
    assert path.read_bytes() == before


def test_atomic_write_uses_independent_staging_files(tmp_path):
    barrier = Barrier(2)
    target = tmp_path / "artifact"

    def publish(value):
        def write(path):
            path.write_text(value)
            barrier.wait(timeout=5)
            assert path.read_text() == value
        atomic_write(target, write)

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(publish, ["one", "two"]))
    assert target.read_text() in {"one", "two"}
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.parametrize("boundary", ["serialize", "replace"])
def test_publication_failure_preserves_complete_model(tmp_path, monkeypatch, boundary):
    path = save_trained(_model("old"), tmp_path)
    before = path.read_bytes()

    def fail(*args, **kwargs):
        raise OSError("injected write failure")

    if boundary == "serialize":
        monkeypatch.setattr(joblib, "dump", fail)
    else:
        monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError, match="injected write failure"):
        store.write_archive(_model("new"), path)
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]


def test_legacy_restore_uses_payload_not_stale_json(tmp_path):
    legacy = tmp_path / "logreg.joblib"
    joblib.dump(_model("legacy"), legacy)
    twin = tmp_path / "logreg.json"
    twin.write_text(json.dumps({"version": "stale"}))
    assert store.model_path(tmp_path, "logreg") == legacy
    assert store.read_metadata(legacy)["version"] == "legacy"
    snapshot = snapshot_current(tmp_path, "logreg")
    save_trained(_model("new"), tmp_path)
    restored = restore_snapshot(tmp_path, "logreg", snapshot.name)
    assert restored.suffix == ".zip"
    assert store.model_path(tmp_path, "logreg") == restored
    assert store.load_trained(restored).golden_csv_sha256 == "legacy"
    assert store.read_metadata(restored)["version"] == "legacy"
    assert legacy.is_file() and json.loads(twin.read_text())["version"] == "stale"


def test_corrupt_archive_does_not_fall_back_to_legacy_or_retrain(tmp_path, monkeypatch):
    from zotero_summarizer.services.model import classifier_persistence

    joblib.dump(_model("legacy"), tmp_path / "logreg.joblib")
    (tmp_path / "logreg.zip").write_bytes(b"broken archive")
    golden = tmp_path / "golden.csv"
    golden.write_text("item_key,priority\n")

    def forbidden(*args, **kwargs):
        pytest.fail("corrupt archive must not trigger implicit retraining")

    monkeypatch.setattr(classifier_persistence, "train_and_save", forbidden)
    with pytest.raises(BadZipFile):
        classifier_persistence.load_or_train(
            golden, classifier_name="logreg", corpus_db_path=tmp_path / "corpus.db",
            goals_config=None, output_dir=tmp_path,
        )


def test_load_rejects_mismatched_archive_metadata(tmp_path):
    path = save_trained(_model("old"), tmp_path)
    with ZipFile(path) as archive:
        payload = archive.read("model.joblib")
    with ZipFile(path, "w") as archive:
        archive.writestr("model.joblib", payload)
        archive.writestr("metadata.json", '{"version":"different"}')
    with pytest.raises(ValueError, match="does not match"):
        store.load_trained(path)


def test_concurrent_archives_publish_matching_payload_and_metadata(tmp_path, monkeypatch):
    path = tmp_path / "logreg.zip"
    barrier = Barrier(2)
    replace = os.replace

    def publish(source, destination):
        barrier.wait(timeout=5)
        replace(source, destination)

    monkeypatch.setattr(os, "replace", publish)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda version: store.write_archive(_model(version), path), ["one", "two"]))
    trained = store.load_trained(path)
    assert store.read_metadata(path)["version"] == trained.golden_csv_sha256
    assert list(tmp_path.iterdir()) == [path]


def test_process_exit_mid_archive_preserves_live_model(tmp_path):
    path = save_trained(_model("old"), tmp_path)
    before = path.read_bytes()
    script = """
import os, sys
from pathlib import Path
from zotero_summarizer.services.model import classifier_store as store
path = Path(sys.argv[1])
trained = store.load_trained(path)
trained.golden_csv_sha256 = "new"
store.json.dumps = lambda *args, **kwargs: os._exit(17)
store.write_archive(trained, path)
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(path)],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, timeout=30,
    )
    assert result.returncode == 17, result.stderr.decode()
    assert path.read_bytes() == before
    assert store.load_trained(path).golden_csv_sha256 == "old"
