from __future__ import annotations

import asyncio

from zotero_summarizer.domain import ChangeStatus
from zotero_summarizer.models import PendingChangeMutationRequest
from zotero_summarizer.services import corpus as corpus_service
from zotero_summarizer.services.zotero import pending as pending_service
from zotero_summarizer.services.zotero import zotero as zotero_service


class _FakeWriter:
    def __init__(self) -> None:
        self.applied_changes: list[dict] | None = None

    def is_connector_running(self) -> bool:
        return False

    def apply_changes(self, changes, backup):  # noqa: ARG002 - mirror the real signature
        self.applied_changes = list(changes)
        return {
            "applied_ids": [int(c["id"]) for c in changes],
            "failed": [],
            "backup_path": "/tmp/backup.sqlite",
        }


def _install_capture(monkeypatch):
    """Capture the status passed to get_pending_changes_by_ids + a fake writer."""
    captured: dict = {"status": None}
    writer = _FakeWriter()

    def fake_get_by_ids(change_ids, status=None):
        captured["status"] = status
        return [{"id": int(cid), "item_key": f"KEY{cid}", "change_type": "tag_changes"} for cid in change_ids]

    monkeypatch.setattr(pending_service.triage_db, "get_pending_changes_by_ids", fake_get_by_ids)
    monkeypatch.setattr(pending_service.triage_db, "set_pending_changes_status", lambda *a, **k: len(a[0]))
    monkeypatch.setattr(zotero_service, "get_zotero_writer_or_raise", lambda: writer)

    async def _noop_refresh(item_keys):  # noqa: ARG001
        return (0, 0, 0)

    monkeypatch.setattr(corpus_service, "refresh_corpus_items_by_keys", _noop_refresh)
    return captured, writer


def test_apply_with_retry_fetches_failed_changes(monkeypatch):
    captured, writer = _install_capture(monkeypatch)

    req = PendingChangeMutationRequest(change_ids=[1, 2], retry=True)
    result = asyncio.run(pending_service.apply_pending_changes(req))

    assert captured["status"] == ChangeStatus.FAILED.value
    assert result["applied"] == 2
    assert writer.applied_changes is not None
    assert {int(c["id"]) for c in writer.applied_changes} == {1, 2}


def test_apply_without_retry_uses_pending_status(monkeypatch):
    captured, writer = _install_capture(monkeypatch)

    req = PendingChangeMutationRequest(change_ids=[3], retry=False)
    result = asyncio.run(pending_service.apply_pending_changes(req))

    assert captured["status"] == ChangeStatus.PENDING.value
    assert result["applied"] == 1
    assert writer.applied_changes is not None


def test_retry_flag_defaults_to_false():
    req = PendingChangeMutationRequest(change_ids=[1])
    assert req.retry is False
