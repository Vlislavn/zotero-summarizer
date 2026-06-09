"""Explicit ``label:<priority>`` ground-truth tag: detection, precedence,
write-side mutual exclusion, export reconcile, and the one-time migration CLI."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

from tests._zotero_fixtures import (
    add_library_item,
    add_tag_to_item,
    build_zotero_db,
    mark_trashed,
)
from zotero_summarizer import domain
from zotero_summarizer.services.golden import goldenset, user_labels
from zotero_summarizer.services.zotero.pending import build_label_tag_change
from zotero_summarizer.storage.repositories import (
    TriageRepository,
    get_label_verdict,
    insert_or_update_label_verdict,
)


# --- domain helpers --------------------------------------------------------


def test_label_tag_roundtrip_and_rejects_unknown():
    assert domain.label_tag_for_priority("could_read") == "label:could_read"
    assert domain.priority_from_label_tag("label:could_read") == "could_read"
    # prefix is case-insensitive; the class must be valid
    assert domain.priority_from_label_tag("Label:Must_Read") == "must_read"
    assert domain.priority_from_label_tag("label:bogus") is None
    assert domain.priority_from_label_tag("zs:rel/must_read") is None
    assert domain.priority_from_label_tag("") is None


# --- detection -------------------------------------------------------------


def test_detect_label_none_single_and_highest_wins():
    assert user_labels.detect_label(["🧠", "👀"]) is None
    assert user_labels.detect_label(["🧠", "label:should_read"]) == "should_read"
    # a stray leftover never silently downgrades a deliberate higher label
    assert user_labels.detect_label(["label:could_read", "label:must_read"]) == "must_read"


# --- precedence in _infer_label -------------------------------------------


def test_infer_label_override_beats_trash_and_emoji():
    priority, strength, relevance, tier = goldenset._infer_label(
        tags=["label:must_read", "🥱"],  # hard-veto emoji present...
        in_trash=True,                    # ...and trashed...
        note_count=0,
        annotation_count=0,
    )
    # ...the explicit label still wins (top precedence).
    assert (priority, strength, tier) == ("must_read", "high", "user_label")
    assert relevance == domain.PRIORITY_TO_RELEVANCE["must_read"]


def test_infer_label_label_dont_read_overrides_positive_emoji():
    priority, _, relevance, tier = goldenset._infer_label(
        tags=["label:dont_read", "🧠"],  # 🧠 alone would be must_read
        in_trash=False,
        note_count=3,
        annotation_count=5,
    )
    assert (priority, tier) == ("dont_read", "user_label")
    assert relevance == domain.PRIORITY_TO_RELEVANCE["dont_read"]


def test_infer_label_falls_back_to_derivation_without_label():
    # No label tag → existing behavior unchanged ("act as before").
    priority, _, _, tier = goldenset._infer_label(
        tags=["🧠"], in_trash=False, note_count=0, annotation_count=0,
    )
    assert priority == "must_read"
    assert tier != "user_label"


# --- write-side mutual exclusion ------------------------------------------


def test_build_label_tag_change_adds_when_absent():
    payload = build_label_tag_change(["🧠", "topic:x"], "could_read")
    assert payload == {"add_tags": ["label:could_read"], "remove_tags": []}


def test_build_label_tag_change_is_idempotent():
    payload = build_label_tag_change(["label:could_read"], "could_read")
    assert payload == {"add_tags": [], "remove_tags": []}


def test_build_label_tag_change_swaps_within_namespace_only():
    payload = build_label_tag_change(
        ["label:could_read", "zs:rel/must_read", "🧠", "topic:x"], "must_read",
    )
    assert payload["add_tags"] == ["label:must_read"]
    # only the other label:* tag is removed — emoji / zs:rel/ / topical untouched
    assert payload["remove_tags"] == ["label:could_read"]


# --- export reconcile ------------------------------------------------------


def _verdict_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "triage_history.db"
    TriageRepository(db_path).init()
    return db_path


def _sample(item_key: str, priority: str) -> SimpleNamespace:
    return SimpleNamespace(
        item_key=item_key, gold_signal_tier="user_label", gold_priority_inferred=priority,
    )


def _seed_verdict(triage_db: Path, item_key: str, priority: str) -> None:
    insert_or_update_label_verdict(
        triage_db, item_key=item_key, original_derived_priority="zotero_label",
        user_priority=priority, comment="",
    )


def test_reconcile_mirrors_user_label_tier_and_is_idempotent(tmp_path):
    db_path = _verdict_db(tmp_path)
    zdb = build_zotero_db(tmp_path / "zotero")
    samples = [
        _sample("AAAA1111", "must_read"),
        SimpleNamespace(item_key="BBBB2222", gold_signal_tier="strong_positive",
                        gold_priority_inferred="should_read"),  # not a label → ignored
    ]
    counts = user_labels.reconcile_label_verdicts(samples, zdb, db_path)
    assert counts.synced == 1
    assert get_label_verdict(db_path, "AAAA1111")["user_priority"] == "must_read"
    assert get_label_verdict(db_path, "BBBB2222") is None
    # second pass is a no-op (already in sync; AAAA1111 absent from Zotero → never retracted)
    assert user_labels.reconcile_label_verdicts(samples, zdb, db_path).synced == 0


def test_reconcile_updates_stale_verdict_and_counts_drift(tmp_path):
    db_path = _verdict_db(tmp_path)
    zdb = build_zotero_db(tmp_path / "zotero")
    user_labels.reconcile_label_verdicts([_sample("CCCC3333", "could_read")], zdb, db_path)
    # the Zotero label later changes to must_read → reconcile must win + count the drift
    counts = user_labels.reconcile_label_verdicts([_sample("CCCC3333", "must_read")], zdb, db_path)
    assert counts.changed == 1
    assert get_label_verdict(db_path, "CCCC3333")["user_priority"] == "must_read"


# --- retraction (#5): removing the label:* tag in Zotero retracts the verdict ----


def test_reconcile_retracts_when_label_tag_removed(tmp_path):
    db_path = _verdict_db(tmp_path)
    zdb = build_zotero_db(tmp_path / "zotero")
    # item present in Zotero, but its label:* tag has been removed (no label tag)
    add_library_item(zdb, item_key="DROPLBL1", title="dropped")
    _seed_verdict(db_path, "DROPLBL1", "could_read")
    counts = user_labels.reconcile_label_verdicts([], zdb, db_path)
    assert counts.removed == 1
    assert get_label_verdict(db_path, "DROPLBL1") is None


def test_reconcile_keeps_verdict_when_label_still_present(tmp_path):
    db_path = _verdict_db(tmp_path)
    zdb = build_zotero_db(tmp_path / "zotero")
    item_id = add_library_item(zdb, item_key="KEEPLBL1", title="kept")
    add_tag_to_item(zdb, item_id=item_id, tag_name="label:could_read")
    _seed_verdict(db_path, "KEEPLBL1", "could_read")
    counts = user_labels.reconcile_label_verdicts([], zdb, db_path)
    assert counts.removed == 0
    assert get_label_verdict(db_path, "KEEPLBL1") is not None


def test_reconcile_keeps_verdict_when_item_missing_from_zotero(tmp_path):
    db_path = _verdict_db(tmp_path)
    zdb = build_zotero_db(tmp_path / "zotero")  # item never added → missing
    _seed_verdict(db_path, "GONE0001", "must_read")
    counts = user_labels.reconcile_label_verdicts([], zdb, db_path)
    assert counts.removed == 0
    assert get_label_verdict(db_path, "GONE0001") is not None  # transient absence must not lose it


def test_reconcile_keeps_verdict_when_item_trashed(tmp_path):
    db_path = _verdict_db(tmp_path)
    zdb = build_zotero_db(tmp_path / "zotero")
    item_id = add_library_item(zdb, item_key="TRASHED1", title="trashed")
    mark_trashed(zdb, item_id=item_id)  # in trash → not a deliberate tag removal
    _seed_verdict(db_path, "TRASHED1", "dont_read")
    counts = user_labels.reconcile_label_verdicts([], zdb, db_path)
    assert counts.removed == 0
    assert get_label_verdict(db_path, "TRASHED1") is not None


def test_reconcile_never_retracts_feed_or_note_verdicts(tmp_path):
    db_path = _verdict_db(tmp_path)
    zdb = build_zotero_db(tmp_path / "zotero")  # no such items in the library
    _seed_verdict(db_path, "feed:42", "should_read")
    _seed_verdict(db_path, "note:ABCD1234:7", "could_read")
    counts = user_labels.reconcile_label_verdicts([], zdb, db_path)
    assert counts.removed == 0
    assert get_label_verdict(db_path, "feed:42") is not None
    assert get_label_verdict(db_path, "note:ABCD1234:7") is not None


def test_reconcile_never_retracts_app_typed_verdict(tmp_path):
    # A verdict typed in the Annotate UI carries its DERIVED original (not the
    # ZOTERO_LABEL_ORIGIN marker), so it must survive even on a present, tag-free
    # item — these are the hundreds of in-app verdicts that must never be wiped
    # just because they were never pushed out as Zotero tags.
    db_path = _verdict_db(tmp_path)
    zdb = build_zotero_db(tmp_path / "zotero")
    add_library_item(zdb, item_key="APPONLY1", title="typed in app")  # present, no label tag
    insert_or_update_label_verdict(
        db_path, item_key="APPONLY1",
        original_derived_priority="could_read",  # Annotate-UI origin, NOT 'zotero_label'
        user_priority="must_read", comment="",
    )
    counts = user_labels.reconcile_label_verdicts([], zdb, db_path)
    assert counts.removed == 0
    assert get_label_verdict(db_path, "APPONLY1") is not None


# --- migration CLI ---------------------------------------------------------


class _FakeReader:
    def __init__(self, _data_dir):
        self._by_key = {
            "ABCD1234": {"tags": ["topic:x"]},                 # needs label:could_read
            "EFGH5678": {"tags": ["label:must_read"]},         # already in sync
            "DEAD0000": None,                                  # missing in Zotero
        }

    def get_item_detail(self, item_key):
        return self._by_key.get(item_key)


class _FakeWriter:
    instances: list = []

    def __init__(self, _data_dir):
        self.last_changes = None
        _FakeWriter.instances.append(self)

    def is_connector_running(self):
        return False

    def apply_changes(self, changes, create_backup):
        self.last_changes = list(changes)
        self.create_backup = create_backup
        return {"applied_ids": [0], "failed": [], "backup_path": "/tmp/backup"}


def _patch_migrate(monkeypatch, tmp_path, writer_cls=_FakeWriter):
    from zotero_summarizer.cli import _goldenset_migrate as mig
    from zotero_summarizer.integrations import zotero_read, zotero_write
    from zotero_summarizer.storage import repositories

    verdicts = [
        {"item_key": "ABCD1234", "user_priority": "could_read"},  # library, needs write
        {"item_key": "EFGH5678", "user_priority": "must_read"},   # already in sync
        {"item_key": "feed:42", "user_priority": "dont_read"},    # not a library item
        {"item_key": "DEAD0000", "user_priority": "should_read"}, # gone from Zotero
    ]
    fake_settings = SimpleNamespace(zotero_data_dir=tmp_path, triage_db_path=tmp_path / "t.db")
    monkeypatch.setattr(mig.Settings, "load", staticmethod(lambda project_root=None: fake_settings))
    monkeypatch.setattr(repositories, "list_label_verdicts", lambda *_a, **_k: verdicts)
    monkeypatch.setattr(zotero_read, "ZoteroReader", _FakeReader)
    monkeypatch.setattr(zotero_write, "ZoteroWriter", writer_cls)
    return mig


def test_migrate_dry_run_plans_without_writing(monkeypatch, tmp_path, capsys):
    class _ExplodingWriter:
        def __init__(self, *_a, **_k):
            raise AssertionError("dry-run must not construct a writer")

    mig = _patch_migrate(monkeypatch, tmp_path, writer_cls=_ExplodingWriter)
    args = argparse.Namespace(project_root=None, dry_run=True, force=False)
    rc = mig._goldenset_migrate_verdicts(args)
    assert rc == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["to_write"] == 1
    assert summary["already_in_sync"] == 1
    assert summary["skipped_non_library"] == 1
    assert summary["skipped_missing_in_zotero"] == 1
    assert summary["planned"][0]["item_key"] == "ABCD1234"
    assert summary["planned"][0]["add_tags"] == ["label:could_read"]


def test_migrate_apply_writes_planned_changes(monkeypatch, tmp_path, capsys):
    _FakeWriter.instances.clear()
    mig = _patch_migrate(monkeypatch, tmp_path)
    args = argparse.Namespace(project_root=None, dry_run=False, force=False)
    rc = mig._goldenset_migrate_verdicts(args)
    assert rc == 0
    writer = _FakeWriter.instances[-1]
    assert writer.create_backup is True  # single backup for the batch
    assert len(writer.last_changes) == 1
    assert writer.last_changes[0]["item_key"] == "ABCD1234"
    assert writer.last_changes[0]["payload_json"]["add_tags"] == ["label:could_read"]
    summary = json.loads(capsys.readouterr().out)
    assert summary["applied"] == 1
