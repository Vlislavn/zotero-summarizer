"""Owned current-verdict claim gaps, outcomes and history authority; no live library."""
from contextlib import closing
from copy import deepcopy
import json
import sqlite3

import pytest

from tests import test_verdict_mirror_receipts as fixture
from tests._direct_write_fixtures import native
from tests._zotero_fixtures import add_library_item, build_zotero_db
from tests.test_verdict_mirror_receipts import verdict_resources  # noqa: F401
from zotero_summarizer.api.errors import APIError
from zotero_summarizer.integrations.zotero_write import ZoteroWriteError
from zotero_summarizer.services.golden.verdict_effects import mirror_current_verdict
from zotero_summarizer.services.zotero import mirror_notes, pending_confirmation
from zotero_summarizer.storage import pending_actions, repositories


def _save(b, *, deletion=False):
    repositories.insert_or_update_label_verdict(b.queue_db, item_key=fixture.KEY,
        original_derived_priority="could_read", user_priority="must_read", comment=fixture.COMMENT, source="user")
    if deletion:
        mirror_current_verdict(b.queue_db, fixture.KEY)
        repositories.delete_label_verdict(b.queue_db, fixture.KEY)


def _before_dispatch(monkeypatch, mutate):
    actual = mirror_notes._MirrorDelivery._predispatch

    def observe(delivery, claimed):
        assert delivery.source == "mirror_verdict" and not delivery.conn.in_transaction
        assert len(claimed) == 2 and all(row["status"] == "applying" for row in claimed)
        mutate(delivery, claimed)
        return actual(delivery, claimed)

    monkeypatch.setattr(mirror_notes._MirrorDelivery, "_predispatch", observe)


@pytest.mark.parametrize("field", ["value", "comment", "source", "alias"])
def test_committed_claim_rechecks_current_intent_before_native(verdict_resources, monkeypatch, field):
    b = verdict_resources
    _save(b)
    before = native(b.db)

    def mutate(delivery, claimed):
        with closing(sqlite3.connect(b.queue_db)) as conn, conn:
            if field == "alias":
                conn.execute("INSERT INTO processed_feed_items "
                    "(feed_library_id,feed_item_id,stable_feed_key,guid,title,decision,run_id,materialized_zotero_key) "
                    "VALUES (2,42,'feed:g:owned-alias','g','Owned','user_approved','test',?)", (fixture.KEY,))
                conn.row_factory = sqlite3.Row
                repositories.upsert_label_verdict(conn, item_key="feed:g:owned-alias",
                    original_derived_priority="could_read", user_priority="should_read", comment="New alias", source="user")
            else:
                column = {"value": "user_priority", "comment": "comment", "source": "source"}[field]
                value = {"value": "should_read", "comment": "New rationale", "source": "machine_add"}[field]
                conn.execute(f"UPDATE label_verdicts SET {column}=? WHERE item_key=?", (value, fixture.KEY))

    _before_dispatch(monkeypatch, mutate)
    with pytest.raises(APIError) as refused:
        mirror_current_verdict(b.queue_db, fixture.KEY)
    assert refused.value.error == "projection_changed"
    assert native(b.db) == before and b.dispatches == []
    rows = fixture._history(b.queue_db)
    assert len(rows) == 2 and all(row["status"] == "failed" for row in rows)
    assert all(json.loads(row["audit_json"])[-1]["event"] == "mirror_aborted" for row in rows)
    with pytest.raises(ValueError):
        pending_actions.ensure_pending(rows)


@pytest.mark.parametrize("change", ["native_plan", "native_file"])
def test_claim_gap_rechecks_native_plan_and_target(verdict_resources, monkeypatch, change):
    b = verdict_resources
    _save(b)
    expected = []

    def mutate(delivery, claimed):
        if change == "native_plan":
            with closing(sqlite3.connect(b.db)) as conn, conn:
                conn.execute("DELETE FROM itemTags WHERE itemID=? AND tagID IN "
                             "(SELECT tagID FROM tags WHERE name=?)", (b.parent, fixture.OLD_LABEL))
        else:
            b.db.rename(b.db.with_name("preserved-before-replacement.sqlite"))
            replacement = build_zotero_db(b.db.parent)
            add_library_item(replacement, item_key=fixture.KEY, title="Replacement target")
        expected.append(native(b.db))

    _before_dispatch(monkeypatch, mutate)
    with pytest.raises(APIError) as refused:
        mirror_current_verdict(b.queue_db, fixture.KEY)
    assert refused.value.error == ("projection_changed" if change == "native_plan" else "target_changed")
    assert native(b.db) == expected[0] and b.dispatches == []
    assert all(row["status"] == "failed" for row in fixture._history(b.queue_db))


@pytest.mark.parametrize("field", ["payload", "audit", "status", "backup", "item", "missing"])
def test_entire_owned_batch_must_be_unchanged_before_exclusion(verdict_resources, monkeypatch, field):
    b = verdict_resources
    _save(b)
    before = native(b.db)

    def mutate(delivery, claimed):
        ident = claimed[-1]["id"]
        with closing(sqlite3.connect(b.queue_db)) as conn, conn:
            if field == "missing":
                conn.execute("DELETE FROM pending_changes WHERE id=?", (ident,))
            else:
                column, value = {
                    "payload": ("payload_json", '{}'), "audit": ("audit_json", '[]'),
                    "status": ("status", "failed"), "backup": ("backup_path", "changed.sqlite"),
                    "item": ("item_key", fixture.OTHER),
                }[field]
                conn.execute(f"UPDATE pending_changes SET {column}=? WHERE id=?", (value, ident))

    _before_dispatch(monkeypatch, mutate)
    with pytest.raises(ValueError):
        mirror_current_verdict(b.queue_db, fixture.KEY)
    assert b.dispatches == [] and native(b.db) == before


def test_owned_exclusion_does_not_hide_another_applying_claim(verdict_resources, monkeypatch):
    b = verdict_resources
    _save(b)
    before = native(b.db)

    def mutate(delivery, claimed):
        from zotero_summarizer.models.zotero_actions import SystemMirrorRequest
        event = json.loads(claimed[0]["audit_json"])[0]
        request = SystemMirrorRequest.model_validate({k: v for k, v in event.items() if k != "at"}, strict=True)
        delivery.conn.execute("BEGIN IMMEDIATE")
        pending_actions.claim_mirror(delivery.conn, "Other owned claim", request, [delivery.actions[0]])
        delivery.conn.commit()

    _before_dispatch(monkeypatch, mutate)
    with pytest.raises(APIError) as refused:
        mirror_current_verdict(b.queue_db, fixture.KEY)
    assert refused.value.error == "zotero_write_uncertain"
    assert b.dispatches == [] and native(b.db) == before
    assert len(fixture._history(b.queue_db)) == 3


@pytest.mark.parametrize("deletion", [False, True])
@pytest.mark.parametrize("failure", ["native_ack", "terminal_write"])
def test_uncertain_native_or_terminal_ack_never_replays(verdict_resources, monkeypatch, deletion, failure):
    b = verdict_resources
    _save(b, deletion=deletion)
    prior_dispatch, prior_backup = len(b.dispatches), len(b.backups)
    if failure == "native_ack":
        actual = b.writer.apply_changes

        def lost(*args, **kwargs):
            actual(*args, **kwargs)
            raise OSError("Owned synthetic native ACK loss")

        monkeypatch.setattr(b.writer, "apply_changes", lost)
    else:
        with closing(sqlite3.connect(b.queue_db)) as conn, conn:
            conn.execute("CREATE TRIGGER refuse_terminal BEFORE UPDATE ON pending_changes "
                         "WHEN NEW.status='applied' BEGIN SELECT RAISE(ABORT,'owned terminal refusal'); END")
    with pytest.raises(APIError) as refused:
        mirror_current_verdict(b.queue_db, fixture.KEY)
    assert refused.value.error == "zotero_write_uncertain"
    assert len(b.dispatches) == prior_dispatch + 2 and len(b.backups) == prior_backup + 1
    after = native(b.db)
    rows = fixture._history(b.queue_db)
    applying = [row for row in rows if row["status"] == "applying"]
    assert len(applying) == 2
    assert all([event["event"] for event in json.loads(row["audit_json"])] == ["mirror_requested"] for row in applying)
    if deletion:
        with closing(sqlite3.connect(b.queue_db)) as conn:
            assert conn.execute("SELECT count(*) FROM label_mirror_receipts").fetchone()[0] == 0
    with pytest.raises(APIError) as retry:
        mirror_current_verdict(b.queue_db, fixture.KEY)
    assert retry.value.error == "zotero_write_uncertain"
    assert native(b.db) == after and len(b.dispatches) == prior_dispatch + 2 and len(b.backups) == prior_backup + 1


@pytest.mark.parametrize("component", ["note", "tag"])
def test_known_partial_retry_writes_only_the_unfinished_component(verdict_resources, component):
    b = verdict_resources
    _save(b)
    operation = "INSERT ON itemNotes" if component == "note" else "DELETE ON itemTags"
    with closing(sqlite3.connect(b.db)) as conn, conn:
        conn.execute(f"CREATE TRIGGER refuse_component BEFORE {operation} "
                     "BEGIN SELECT RAISE(ABORT,'owned component refusal'); END")
    with pytest.raises(ZoteroWriteError, match="owned component refusal"):
        mirror_current_verdict(b.queue_db, fixture.KEY)
    statuses = ["failed", "applied"] if component == "note" else ["applied", "failed"]
    assert [row["status"] for row in fixture._history(b.queue_db)] == statuses
    preserved = "tags" if component == "note" else "notes"
    observed = native(b.db)[preserved]
    with closing(sqlite3.connect(b.db)) as conn, conn:
        conn.execute("DROP TRIGGER refuse_component")
    mirror_current_verdict(b.queue_db, fixture.KEY)
    assert [entry["change"]["change_type"] for entry in b.dispatches] == [
        "upsert_note", "tag_changes", "upsert_note" if component == "note" else "tag_changes"]
    assert native(b.db)[preserved] == observed
    assert [row["status"] for row in fixture._history(b.queue_db)] == [*statuses, "applied"]


@pytest.mark.parametrize("verdict_first", [True, False])
def test_composite_and_verdict_sources_do_not_reuse_each_other(verdict_resources, verdict_first):
    b = verdict_resources
    _save(b)
    repositories.upsert_review_note(b.queue_db, fixture.KEY, "Independent manual composite")
    if verdict_first:
        mirror_current_verdict(b.queue_db, fixture.KEY)
    result = mirror_notes.mirror_review_note(b.queue_db, fixture.KEY)
    assert result["note_written"] and result["note_pending"] is False
    if not verdict_first:
        mirror_current_verdict(b.queue_db, fixture.KEY)
    rows = fixture._history(b.queue_db)
    assert {pending_actions.history_request(row)["source"] for row in rows} == {"mirror_user_note", "mirror_verdict"}
    count, backups = len(b.dispatches), len(b.backups)
    mirror_current_verdict(b.queue_db, fixture.KEY)
    mirror_notes.mirror_review_note(b.queue_db, fixture.KEY)
    assert len(b.dispatches) == count and len(b.backups) == backups


def test_original_legacy_alias_is_retained_across_committed_claim_gap(verdict_resources, monkeypatch):
    b = verdict_resources
    with closing(sqlite3.connect(b.queue_db)) as conn, conn:
        for ident, stable, target in [(42, "feed:g:alias-a", fixture.KEY), (43, "feed:g:alias-b", fixture.OTHER)]:
            conn.execute("INSERT INTO processed_feed_items "
                "(feed_library_id,feed_item_id,stable_feed_key,guid,title,decision,run_id,materialized_zotero_key) "
                "VALUES (2,?,?,?,'Owned','user_approved','test',?)", (ident, stable, stable, target))
        conn.execute("INSERT INTO feed_key_aliases(old_key,stable_feed_key) VALUES ('feed:42','feed:g:alias-a')")
    for key, value in [("feed:g:alias-a", "must_read"), ("feed:g:alias-b", "should_read")]:
        repositories.insert_or_update_label_verdict(b.queue_db, item_key=key,
            original_derived_priority="could_read", user_priority=value, comment=fixture.COMMENT, source="user")
    before = native(b.db)

    def mutate(delivery, claimed):
        with closing(sqlite3.connect(b.queue_db)) as conn, conn:
            conn.execute("UPDATE feed_key_aliases SET stable_feed_key='feed:g:alias-b' WHERE old_key='feed:42'")

    _before_dispatch(monkeypatch, mutate)
    with pytest.raises(APIError) as refused:
        mirror_current_verdict(b.queue_db, "feed:42")
    assert refused.value.error == "projection_changed"
    assert native(b.db) == before and b.dispatches == []


@pytest.mark.parametrize("component", ["tag", "note"])
def test_native_completion_is_current_at_deletion_domain_ack(verdict_resources, monkeypatch, component):
    b = verdict_resources
    _save(b, deletion=True)
    actual = mirror_notes._MirrorDelivery._acknowledge

    def changed(delivery, rows):
        assert not delivery.conn.in_transaction and len(rows) == 2
        if component == "tag":
            from tests._zotero_fixtures import add_tag_to_item
            add_tag_to_item(b.db, item_id=b.parent, tag_name=fixture.OLD_LABEL)
        else:
            with closing(sqlite3.connect(b.db)) as conn, conn:
                conn.execute("UPDATE itemNotes SET note=? WHERE parentItemID=? AND note LIKE ?",
                    (fixture.VERDICT_HTML, b.parent, "%zs:note_type=verdict%"))
        return actual(delivery, rows)

    monkeypatch.setattr(mirror_notes._MirrorDelivery, "_acknowledge", changed)
    with pytest.raises(APIError) as refused:
        mirror_current_verdict(b.queue_db, fixture.KEY)
    assert refused.value.error == "projection_changed"
    with closing(sqlite3.connect(b.queue_db)) as conn:
        assert conn.execute("SELECT count(*) FROM label_mirror_receipts").fetchone()[0] == 0
    assert len(b.dispatches) == 4


@pytest.mark.parametrize("component", ["html", "selected_label_case"])
def test_closed_verdict_effect_is_bound_to_declared_intent(verdict_resources, component):
    b = verdict_resources
    _save(b)
    mirror_current_verdict(b.queue_db, fixture.KEY)
    rows = fixture._history(b.queue_db)
    row = deepcopy(rows[0 if component == "html" else 1])
    payload = json.loads(row["payload_json"])
    if component == "html":
        payload["note_html"] = "<p>Unrelated</p><p><em>zs:note_type=verdict;version=1</em></p>"
    else:
        payload = {"add_tags": [], "remove_tags": ["Label:Must_Read"]}
    row["payload_json"] = json.dumps(payload)
    with pytest.raises(ValueError):
        pending_actions.history_request(row)
    assert fixture._history(b.queue_db) == rows and len(b.dispatches) == 2


@pytest.mark.parametrize("status", ["failed", "applying"])
def test_unconfirmed_system_history_grants_no_pending_operations(verdict_resources, monkeypatch, status):
    import asyncio
    from zotero_summarizer.models.api import PendingChangeApplyRequest, PendingChangeMutationRequest, PendingChangeUpdateRequest
    from zotero_summarizer.services.zotero import pending
    b = verdict_resources
    _save(b)
    if status == "failed":
        with closing(sqlite3.connect(b.db)) as conn, conn:
            conn.execute("CREATE TRIGGER refuse_label BEFORE DELETE ON itemTags "
                         "BEGIN SELECT RAISE(ABORT,'owned tag refusal'); END")
        with pytest.raises(ZoteroWriteError):
            mirror_current_verdict(b.queue_db, fixture.KEY)
    else:
        actual = b.writer.apply_changes

        def lost(*args, **kwargs):
            actual(*args, **kwargs)
            raise OSError("Owned synthetic ACK loss")

        monkeypatch.setattr(b.writer, "apply_changes", lost)
        with pytest.raises(APIError):
            mirror_current_verdict(b.queue_db, fixture.KEY)
    rows = fixture._history(b.queue_db)
    selected = [row for row in rows if row["status"] == status]
    assert selected
    ids = [row["id"] for row in selected]
    before, calls, backups = native(b.db), len(b.dispatches), len(b.backups)
    assert all(row["approval_preview"] is None for row in pending_confirmation.present_changes(selected, b.writer))
    operations = [
        (lambda: asyncio.run(pending.update_pending_change(ids[0], PendingChangeUpdateRequest(payload={}))), 404),
        (lambda: asyncio.run(pending.reject_pending_changes(PendingChangeMutationRequest(change_ids=ids))), 409),
    ]
    for retry in [False, True]:
        request = PendingChangeApplyRequest(change_ids=ids, previews={i: "0" * 64 for i in ids}, retry=retry)
        operations.append((lambda req=request: asyncio.run(pending.apply_pending_changes(req)), 409))
    for operation, code in operations:
        with pytest.raises(APIError) as refused:
            operation()
        assert refused.value.status_code == code
        assert fixture._history(b.queue_db) == rows and native(b.db) == before
        assert len(b.dispatches) == calls and len(b.backups) == backups


def test_domain_ack_failure_reuses_terminal_native_success(verdict_resources):
    b = verdict_resources
    _save(b, deletion=True)
    with closing(sqlite3.connect(b.queue_db)) as conn, conn:
        conn.execute("CREATE TRIGGER refuse_domain_ack BEFORE INSERT ON label_mirror_receipts "
                     "BEGIN SELECT RAISE(ABORT,'owned domain refusal'); END")
    with pytest.raises(sqlite3.IntegrityError, match="owned domain refusal"):
        mirror_current_verdict(b.queue_db, fixture.KEY)
    rows = fixture._history(b.queue_db)
    assert len(rows) == 4 and all(row["status"] == "applied" for row in rows)
    before, calls, backups = native(b.db), len(b.dispatches), len(b.backups)
    with closing(sqlite3.connect(b.queue_db)) as conn, conn:
        assert conn.execute("SELECT count(*) FROM label_mirror_receipts").fetchone()[0] == 0
        conn.execute("DROP TRIGGER refuse_domain_ack")
    mirror_current_verdict(b.queue_db, fixture.KEY)
    with closing(sqlite3.connect(b.queue_db)) as conn:
        assert conn.execute("SELECT count(*) FROM label_mirror_receipts").fetchone()[0] == 1
    assert native(b.db) == before and len(b.dispatches) == calls and len(b.backups) == backups


def test_already_clear_deletion_ack_does_not_fabricate_native_history(verdict_resources):
    b = verdict_resources
    _save(b)
    repositories.delete_label_verdict(b.queue_db, fixture.KEY)
    with closing(sqlite3.connect(b.db)) as conn, conn:
        conn.execute("DELETE FROM itemTags WHERE itemID=? AND tagID IN "
                     "(SELECT tagID FROM tags WHERE name=?)", (b.parent, fixture.OLD_LABEL))
    before = native(b.db)
    mirror_current_verdict(b.queue_db, fixture.KEY)
    assert native(b.db) == before and b.dispatches == b.backups == []
    assert fixture._history(b.queue_db) == []
    with closing(sqlite3.connect(b.queue_db)) as conn:
        assert conn.execute("SELECT count(*) FROM label_mirror_receipts").fetchone()[0] == 1


def test_quoted_marker_manual_note_survives_verdict_and_retraction(verdict_resources):
    b = verdict_resources
    quoted = "<p>The token zs:note_type=verdict is quoted, not ownership.</p>"
    with closing(sqlite3.connect(b.db)) as conn, conn:
        conn.execute("UPDATE itemNotes SET note=? WHERE itemID=?", (quoted, b.manual))
    _save(b, deletion=True)
    mirror_current_verdict(b.queue_db, fixture.KEY)
    with closing(sqlite3.connect(b.db)) as conn:
        assert conn.execute("SELECT note FROM itemNotes WHERE itemID=?", (b.manual,)).fetchone()[0] == quoted
    assert len(b.dispatches) == 4


def test_incomplete_native_partition_remains_applying_and_blocks_replay(verdict_resources, monkeypatch):
    b = verdict_resources
    _save(b)
    actual = b.writer.apply_changes

    def incomplete(*args, **kwargs):
        result = actual(*args, **kwargs)
        result["applied_ids"].pop()
        return result

    monkeypatch.setattr(b.writer, "apply_changes", incomplete)
    with pytest.raises(ValueError, match="Incomplete or contradictory"):
        mirror_current_verdict(b.queue_db, fixture.KEY)
    assert len(b.dispatches) == 2 and all(row["status"] == "applying" for row in fixture._history(b.queue_db))
    before = native(b.db)
    with pytest.raises(APIError) as refused:
        mirror_current_verdict(b.queue_db, fixture.KEY)
    assert refused.value.error == "zotero_write_uncertain" and len(b.dispatches) == 2 and native(b.db) == before


def test_malformed_retained_terminal_history_refuses_new_delivery(verdict_resources):
    b = verdict_resources
    _save(b)
    mirror_current_verdict(b.queue_db, fixture.KEY)
    with closing(sqlite3.connect(b.queue_db)) as conn, conn:
        conn.execute("UPDATE pending_changes SET applied_at=NULL WHERE id=(SELECT min(id) FROM pending_changes)")
    before, calls, backups = native(b.db), len(b.dispatches), len(b.backups)
    with pytest.raises(ValueError, match="terminal metadata"):
        mirror_current_verdict(b.queue_db, fixture.KEY)
    assert native(b.db) == before and len(b.dispatches) == calls and len(b.backups) == backups


@pytest.mark.parametrize("field,value", [("status", "pending"), ("applied_at", None),
                                          ("error_message", "invented failure")])
def test_terminal_history_corruption_fails_loud_before_pending_projection(verdict_resources, field, value):
    b = verdict_resources
    _save(b)
    mirror_current_verdict(b.queue_db, fixture.KEY)
    rows = deepcopy(fixture._history(b.queue_db))
    rows[0][field] = value
    before, dispatched = native(b.db), len(b.dispatches)
    with pytest.raises(ValueError):
        pending_confirmation.present_changes(rows, b.writer)
    assert native(b.db) == before and len(b.dispatches) == dispatched
