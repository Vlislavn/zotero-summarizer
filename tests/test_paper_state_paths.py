"""Paper-state keys must not escape storage, including through existing symlinks."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from zotero_summarizer.api.errors import APIError, install_error_handlers
from zotero_summarizer.api.routes import library
from zotero_summarizer.services.library import paper_render
from zotero_summarizer.services.library.review_fleet import fleet


@pytest.fixture
def storage(tmp_path, monkeypatch):
    root = tmp_path / "data" / "render"
    root.mkdir(parents=True)
    monkeypatch.setattr(paper_render, "settings", lambda: SimpleNamespace(paper_render_dir=root))
    monkeypatch.setattr(paper_render, "_JOBS", {})
    return root


def unexpected(*args, **kwargs):
    pytest.fail("invalid key reached scheduling or paper acquisition")


@pytest.mark.parametrize("key", ["", " ", ".", "..", "../escape", "a/b", "a\\b", "a\0b"])
def test_state_rejects_non_component_keys_before_read_or_write(storage, key):
    for operation in (paper_render._read_state, lambda k: paper_render._write_state(k, {"status": "error"})):
        with pytest.raises(APIError) as caught:
            operation(key)
        assert caught.value.status_code == 422
    assert list(storage.iterdir()) == []
    assert not (storage.parent / "paper_read.json").exists()


def test_state_rejects_absolute_key(storage):
    outside = storage.parent / "outside"
    with pytest.raises(APIError) as caught:
        paper_render._write_state(str(outside), {"status": "error"})
    assert caught.value.status_code == 422
    assert not outside.exists()


@pytest.mark.parametrize("link_kind", ["item", "state", "temporary", "dangling", "sibling"])
def test_state_symlinks_cannot_read_or_overwrite_another_file(storage, link_kind):
    outside = storage.parent / "outside"
    outside.mkdir()
    sentinel = outside / "paper_read.json"
    original = '{"status":"completed","secret":"outside"}'
    sentinel.write_text(original)
    item = storage / "KEY1"
    if link_kind == "item":
        item.symlink_to(outside, target_is_directory=True)
    elif link_kind == "sibling":
        sibling = storage / "KEY2"
        sibling.mkdir()
        (sibling / "paper_read.json").write_text(original)
        item.symlink_to(sibling, target_is_directory=True)
    else:
        item.mkdir()
        name = "paper_read.tmp" if link_kind == "temporary" else "paper_read.json"
        target = outside / "missing.json" if link_kind == "dangling" else sentinel
        (item / name).symlink_to(target)
    for operation in (paper_render._read_state, lambda k: paper_render._write_state(k, {"status": "error"})):
        with pytest.raises(APIError) as caught:
            operation("KEY1")
        assert caught.value.status_code == 422
    assert sentinel.read_text() == original
    assert not (outside / "missing.json").exists()


@pytest.mark.parametrize("key", ["KEY1", "feed:123", "feed:d:" + "a" * 64, "note:ABC12345:42"])
def test_valid_keys_round_trip_without_changing_identity(storage, key):
    payload = {"status": "completed", "item_key": key, "audit": {"status": "passed", "blocking": []}}
    assert paper_render._read_state(key) is None
    assert paper_render._write_state(key, payload) == payload
    assert paper_render._read_state(key) == payload
    assert json.loads((storage / key / "paper_read.json").read_text()) == payload
    assert not (storage / key / "paper_read.tmp").exists()


@pytest.mark.parametrize("surface", ["build", "status", "ask", "fleet"])
@pytest.mark.parametrize("key", ["..", "LINK"])
def test_http_rejects_unsafe_keys_before_work_or_job_mutation(storage, monkeypatch, surface, key):
    (storage / "LINK").symlink_to(storage.parent, target_is_directory=True)
    monkeypatch.setattr(paper_render._BUILD_POOL, "submit", unexpected)
    monkeypatch.setattr(paper_render, "_pdf_for_item_or_acquire", unexpected)
    monkeypatch.setattr(paper_render, "_item_detail", unexpected)
    monkeypatch.setattr(fleet, "try_start", unexpected)
    app = FastAPI()
    app.include_router(library.router)
    install_error_handlers(app)
    with TestClient(app) as client:
        encoded = "%2E%2E" if key == ".." else key
        if surface == "build":
            response = client.post(f"/api/library/render/{encoded}/build", json={})
        elif surface == "status":
            response = client.get(f"/api/library/render/{encoded}")
        elif surface == "ask":
            response = client.post("/api/library/ask", json={"item_key": key, "question": "What is this?"})
        else:
            response = client.post("/api/library/review-fleet/run", json={"item_keys": ["VALID", key]})
    assert response.status_code == 422
    assert response.json()["error"] == "validation_error"
    assert paper_render._JOBS == {}
    assert not (storage.parent / "paper_read.json").exists()


def test_sync_build_rejects_key_before_acquisition(storage, monkeypatch):
    monkeypatch.setattr(paper_render, "_pdf_for_item_or_acquire", unexpected)
    with pytest.raises(APIError) as caught:
        paper_render.build_paper_read("..", allow_acquire_missing=True)
    assert caught.value.status_code == 422


@pytest.mark.parametrize("operation", [paper_render.start_build, paper_render.render_paper])
def test_running_job_does_not_bypass_key_validation(storage, operation):
    paper_render._JOBS[".."] = {"status": "running"}
    with pytest.raises(APIError) as caught:
        operation("..")
    assert caught.value.status_code == 422


@pytest.mark.parametrize("error", [OSError("broken PDF"), APIError("needs_pdf", "missing PDF", 404)])
def test_worker_records_and_reraises_original_failure(storage, monkeypatch, error):
    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(paper_render, "build_paper_read", fail)
    with pytest.raises(type(error)) as caught:
        paper_render._build_job("KEY1", force=False, allow_arxiv_source=False, allow_acquire_missing=False)
    assert caught.value is error
    state = paper_render._read_state("KEY1")
    assert state == paper_render._JOBS["KEY1"]
    assert state["status"] == "error"
    if isinstance(error, APIError):
        assert state["error"] == error.error
        assert state["message"] == error.message
        assert state["details"] == error.details
    else:
        assert state["error"] == "OSError: broken PDF"
