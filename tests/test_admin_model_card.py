"""Current-model cards describe the loaded gate, never an unrelated disk run."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from zipfile import ZipFile

import pytest

from zotero_summarizer.api.routes import admin as admin_route
from zotero_summarizer.runtime import AppContext, set_context
from zotero_summarizer.settings import Settings
from zotero_summarizer.services.setup.status import _classifier_status


def _seed(tmp_path, *, loaded=True):
    context = AppContext(settings=Settings.load(project_root=tmp_path))
    context.state.classifier_gate = SimpleNamespace(
        classifier_name="lightgbm",
        training_metadata={
            "n_train": 1171, "trained_at": "2026-05-15T22:15:20Z",
            "oof_spearman_verified": 0.14,
        },
    ) if loaded else None
    set_context(context)
    return context


def _archive(context, name, metadata):
    model_dir = context.settings.model_dir
    model_dir.mkdir(parents=True, exist_ok=True)
    with ZipFile(model_dir / f"{name}.zip", "w") as archive:
        archive.writestr("model.joblib", b"not a pickle")
        archive.writestr("metadata.json", json.dumps({"classifier_name": name, **metadata}))


def test_current_card_reads_live_metadata_without_disk(tmp_path):
    _seed(tmp_path)
    assert asyncio.run(admin_route.model_card()) == {"model": {
        "classifier_name": "lightgbm", "n_train": 1171,
        "trained_at": "2026-05-15T22:15:20Z", "oof_spearman_verified": 0.14,
    }}


def test_no_loaded_gate_does_not_claim_saved_model_is_active(tmp_path):
    context = _seed(tmp_path, loaded=False)
    _archive(context, "lightgbm", {"n_train": 50})
    assert asyncio.run(admin_route.model_card()) == {"model": None}


@pytest.mark.parametrize("offline_name", ["logreg", "lightgbm"])
def test_offline_retrain_does_not_replace_live_card(tmp_path, offline_name):
    context = _seed(tmp_path)
    context.state.app_state = SimpleNamespace(config=SimpleNamespace(
        classifier_gate=SimpleNamespace(model_name="logreg"),
    ))
    _archive(context, offline_name, {
        "n_train": 9999, "trained_at": "2099-01-01T00:00:00Z",
        "oof_spearman_verified": 0.99,
    })
    card = asyncio.run(admin_route.model_card())["model"]
    assert card["classifier_name"] == "lightgbm"
    assert card["n_train"] == 1171
    assert card["oof_spearman_verified"] == 0.14


def test_unrelated_or_truncated_run_log_cannot_change_card(tmp_path):
    context = _seed(tmp_path)
    _archive(context, "lightgbm", {"n_train": 12})
    log = context.settings.data_dir / "classifier-runs.jsonl"
    log.write_text('{"classifier":"lightgbm","cv":{"n_train":12}}\n{"truncated":')
    card = asyncio.run(admin_route.model_card())["model"]
    assert card["n_train"] == 1171
    assert "runlog" not in card


@pytest.mark.parametrize("loaded", [True, False])
def test_setup_classifier_status_uses_same_live_source(tmp_path, loaded):
    context = _seed(tmp_path, loaded=loaded)
    _archive(context, "logreg", {"trained_at": "2099-01-01T00:00:00Z"})
    status = asyncio.run(_classifier_status())
    assert status.trained is loaded
    assert status.classifier_name == ("lightgbm" if loaded else None)


def test_hot_swap_changes_card_without_file_write(tmp_path):
    context = _seed(tmp_path)
    before = asyncio.run(admin_route.model_card())["model"]
    context.state.classifier_gate = SimpleNamespace(
        classifier_name="logreg",
        training_metadata={"n_train": 2000, "trained_at": "2026-09-05T12:00:00Z"},
    )
    after = asyncio.run(admin_route.model_card())["model"]
    assert before["classifier_name"] == "lightgbm"
    assert after["classifier_name"] == "logreg"
    assert after["n_train"] == 2000
    assert after["oof_spearman_verified"] is None
