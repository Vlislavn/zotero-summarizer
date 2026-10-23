"""Reject is one complete app-queue selection, never a partial success."""
import asyncio
import hashlib
from contextlib import closing
from copy import deepcopy

import pytest

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.models import PendingChangeMutationRequest
from zotero_summarizer.models.zotero_actions import DigestNotePayload, GeneratedNoteProposal
from zotero_summarizer.services.zotero import pending
from zotero_summarizer.storage import pending_actions, repositories


@pytest.fixture
def queue(tmp_path):
    with repositories.with_db_path(tmp_path / "queue.sqlite"):
        repositories.init_db()
        repositories.insert_pending_changes("FIRST001", "First", [{"change_type": "add_note", "payload": {"note": "A"}}])
        repositories.insert_pending_changes("SECOND01", "Second", [{"change_type": "add_note", "payload": {"note": "B"}}])
        ids = sorted(row["id"] for row in repositories.get_pending_changes(status=None))
        yield ids


def reject(ids):
    return asyncio.run(pending.reject_pending_changes(PendingChangeMutationRequest(change_ids=ids)))


@pytest.mark.parametrize("state", ["missing", "applied", "rejected", "failed", "applying"])
def test_stale_or_missing_sibling_refuses_entire_selection_without_mutation(queue, state, record_property):
    first, second = queue
    if state == "missing":
        selected = [first, second + 10000]
    else:
        repositories.set_pending_changes_status([second], state)
        selected = [first, second]
    before = deepcopy(repositories.get_pending_changes(status=None))
    error = None
    result = None
    try:
        result = reject(selected)
    except APIError as exc:
        error = exc
    after = repositories.get_pending_changes(status=None)
    record_property("complete_rejection", {"selected": selected, "before": before, "after": after,
                                         "result": result, "status": error.status_code if error else None})
    assert after == before
    assert error is not None and error.status_code == 409


def test_current_complete_selection_returns_exact_count_and_leaves_other_rows(queue):
    first, second = queue
    assert reject([first]) == {"updated": 1}
    rows = {row["id"]: row["status"] for row in repositories.get_pending_changes(status=None)}
    assert rows == {first: "rejected", second: "pending"}
    assert reject([second]) == {"updated": 1}


def test_mixed_generated_and_ordinary_rejection_is_one_audited_selection(queue):
    first, second = queue
    body = '<p>Owned synthetic note</p>'
    generated = pending_actions.stage('Synthetic', GeneratedNoteProposal(
        item_key='THIRD001', review_revision='00000000-0000-0000-0000-000000000001',
        source_sha256=hashlib.sha256(b'synthetic source').hexdigest(),
        body_sha256=hashlib.sha256(body.encode()).hexdigest(),
        payload=DigestNotePayload(marker='zs:note_type=digest', note_html=body),
    ))
    assert reject([first, generated]) == {'updated': 2}
    rows = {row['id']: row for row in repositories.get_pending_changes(status=None)}
    assert rows[first]['status'] == rows[generated]['status'] == 'rejected'
    assert rows[second]['status'] == 'pending'
    assert any(event['event'] == 'rejected' for event in rows[generated]['audit'])


def test_mixed_stale_sibling_leaves_generated_audit_and_ordinary_untouched(queue):
    first, second = queue
    body = '<p>Owned synthetic note</p>'
    generated = pending_actions.stage('Synthetic', GeneratedNoteProposal(
        item_key='THIRD001', review_revision='00000000-0000-0000-0000-000000000001',
        source_sha256=hashlib.sha256(b'synthetic source').hexdigest(),
        body_sha256=hashlib.sha256(body.encode()).hexdigest(),
        payload=DigestNotePayload(marker='zs:note_type=digest', note_html=body),
    ))
    repositories.set_pending_changes_status([second], 'failed')
    before = repositories.get_pending_changes(status=None)
    with pytest.raises(APIError) as error:
        reject([first, generated, second])
    assert error.value.status_code == 409
    assert repositories.get_pending_changes(status=None) == before


def test_trigger_preventing_one_update_rolls_back_the_entire_selection(queue):
    first, second = queue
    with closing(repositories._get_conn()) as conn:
        conn.execute(f"""CREATE TRIGGER prevent_reject BEFORE UPDATE OF status ON pending_changes
            WHEN OLD.id={second} BEGIN SELECT RAISE(IGNORE); END""")
        conn.commit()
    before = repositories.get_pending_changes(status=None)
    error = None
    try:
        reject([first, second])
    except APIError as exc:
        error = exc
    assert repositories.get_pending_changes(status=None) == before
    assert error is not None and error.status_code == 409


def test_empty_api_selection_is_refused_before_database_io(monkeypatch):
    def forbidden():
        raise AssertionError("Empty selection must not open the queue")
    from zotero_summarizer.storage import _repo_pending
    monkeypatch.setattr(_repo_pending, "_get_conn", forbidden)
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        reject([])
