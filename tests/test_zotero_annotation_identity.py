"""Native annotation coordinates survive the existing readonly detail projection."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest

from tests._zotero_fixtures import add_library_item, build_zotero_db
from zotero_summarizer.integrations.zotero_read import ZoteroReadError, ZoteroReader


ADDED = "2026-10-01 10:00:00"
MODIFIED = "2026-10-02 11:00:00"
LEGACY_FIELDS = ("text", "comment", "page_label", "type", "color", "date_added")


@pytest.fixture(autouse=True)
def loaded_source_receipt(request, tmp_path):
    root = Path(__file__).resolve().parents[1]
    names = [
        "zotero_summarizer.integrations.zotero_read",
        "zotero_summarizer.integrations._zotero_read_items",
        "tests._zotero_fixtures", __name__,
    ]
    loaded = {}
    for name in names:
        module = sys.modules[name]
        path = Path(module.__file__).resolve()
        assert path.is_relative_to(root)
        loaded[name] = {
            "path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    receipt = {
        "case_id": request.node.nodeid, "test_root": str(root), "loaded": loaded,
    }
    (tmp_path / "loaded-source.json").write_text(json.dumps(receipt, indent=2) + "\n")


def _attachment(db, parent, key, content_type="application/pdf"):
    item = add_library_item(db, item_key=key, title=key, item_type="attachment")
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO itemAttachments(itemID, parentItemID, contentType) VALUES (?, ?, ?)",
            (item, parent, content_type),
        )
    return item


def _annotation(db, attachment, key, *, text="identical highlighted evidence",
                comment="Не подтверждает claim", added=ADDED, modified=MODIFIED,
                kind=1, color="#ffff00", page="3"):
    item = add_library_item(db, item_key=key, title=key, item_type="annotation")
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE items SET dateAdded = ?, dateModified = ? WHERE itemID = ?",
            (added, modified, item),
        )
        conn.execute(
            "INSERT INTO itemAnnotations(itemID, parentItemID, type, text, comment, color, pageLabel) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (item, attachment, kind, text, comment, color, page),
        )
    return item


def _detail_without_writes(db, key="PARENT01"):
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    detail = ZoteroReader(db.parent).get_item_detail(key)
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before
    return detail


@pytest.fixture
def owned_db(tmp_path):
    db = build_zotero_db(tmp_path / "zotero")
    parent = add_library_item(db, item_key="PARENT01", title="Parent")
    return db, parent


def test_identical_legacy_records_keep_distinct_native_keys(owned_db):
    db, parent = owned_db
    attachment = _attachment(db, parent, "ATTACH01")
    for key in ("ANNOT001", "ANNOT002"):
        _annotation(db, attachment, key)

    annotations = _detail_without_writes(db)["annotations"]

    legacy = {
        "text": "identical highlighted evidence", "comment": "Не подтверждает claim",
        "page_label": "3", "type": "highlight", "color": "#ffff00", "date_added": ADDED,
    }
    assert len(annotations) == 2
    assert [{field: row[field] for field in LEGACY_FIELDS} for row in annotations] == [legacy, legacy]
    assert {row["annotation_key"] for row in annotations} == {"ANNOT001", "ANNOT002"}
    assert all(row["attachment_key"] == "ATTACH01" for row in annotations)
    assert all(row["date_modified"] == MODIFIED for row in annotations)
    assert all(set(row) == set(LEGACY_FIELDS) | {
        "annotation_key", "attachment_key", "date_modified",
    } for row in annotations)


def test_attachment_association_order_and_legacy_values(owned_db):
    db, parent = owned_db
    pdf_a = _attachment(db, parent, "PDF00001")
    pdf_b = _attachment(db, parent, "PDF00002")
    html = _attachment(db, parent, "HTML0001", "text/html")
    _annotation(db, pdf_b, "ANNOT003", text=None, comment="Not useful", added="2026-10-03 12:00:00",
                kind=99, color=None, page=None)
    _annotation(db, html, "ANNOT001", text="Claims benefit", comment="Нет", kind=2, color="#123456", page="iv")
    _annotation(db, pdf_a, "ANNOT002", text="Paper quote", comment=None, added="2026-10-02 10:00:00", kind=5)

    annotations = _detail_without_writes(db)["annotations"]

    assert [(row["annotation_key"], row["attachment_key"]) for row in annotations] == [
        ("ANNOT001", "HTML0001"), ("ANNOT002", "PDF00001"), ("ANNOT003", "PDF00002"),
    ]
    assert [{field: row[field] for field in LEGACY_FIELDS} for row in annotations] == [
        {"text": "Claims benefit", "comment": "Нет", "page_label": "iv", "type": "note",
         "color": "#123456", "date_added": ADDED},
        {"text": "Paper quote", "comment": "", "page_label": "3", "type": "underline",
         "color": "#ffff00", "date_added": "2026-10-02 10:00:00"},
        {"text": "", "comment": "Not useful", "page_label": "", "type": "unknown_99",
         "color": "", "date_added": "2026-10-03 12:00:00"},
    ]
    assert all(row["date_modified"] == MODIFIED for row in annotations)


@pytest.mark.parametrize("modified", ["2026-10-03 12:34:56", MODIFIED], ids=["new-clock", "same-clock"])
@pytest.mark.parametrize("comment", ["Не полезно", "Not relevant"], ids=["ru", "en"])
def test_same_count_content_edits_are_literal_with_raw_clock(tmp_path, modified, comment):
    # Both owned source states are seeded before observation; no payload or observed clock rewriting.
    databases = []
    for state, text, note, clock in [
        ("before", "Earlier paper quote", "Earlier comment", MODIFIED),
        ("after", "Positive highlighted evidence", comment, modified),
    ]:
        db = build_zotero_db(tmp_path / state)
        parent = add_library_item(db, item_key="PARENT01", title="Parent")
        attachment = _attachment(db, parent, "ATTACH01")
        _annotation(db, attachment, "ANNOT001", text=text, comment=note, modified=clock)
        databases.append(db)

    before, after = [_detail_without_writes(db)["annotations"] for db in databases]

    assert len(before) == len(after) == 1
    assert before[0]["text"] == "Earlier paper quote"
    assert before[0]["comment"] == "Earlier comment"
    assert after[0]["text"] == "Positive highlighted evidence"
    assert after[0]["comment"] == comment
    assert before[0]["annotation_key"] == after[0]["annotation_key"] == "ANNOT001"
    assert before[0]["attachment_key"] == after[0]["attachment_key"] == "ATTACH01"
    assert before[0]["date_added"] == after[0]["date_added"] == ADDED
    assert before[0]["date_modified"] == MODIFIED
    assert after[0]["date_modified"] == modified


def test_identity_only_exposes_live_annotations_on_live_attachments(owned_db):
    db, parent = owned_db
    live = _attachment(db, parent, "ATTACH01")
    deleted = _attachment(db, parent, "ATTACH02")
    _annotation(db, live, "ANNOT001")
    trashed_annotation = _annotation(db, live, "ANNOT002")
    _annotation(db, deleted, "ANNOT003")
    with sqlite3.connect(db) as conn:
        conn.executemany("INSERT INTO deletedItems(itemID) VALUES (?)", [(deleted,), (trashed_annotation,)])

    annotations = _detail_without_writes(db)["annotations"]

    assert [(row["annotation_key"], row["attachment_key"]) for row in annotations] == [("ANNOT001", "ATTACH01")]


@pytest.mark.parametrize("scope", ["missing", "deleted", "group"], ids=["missing", "deleted", "group"])
def test_annotation_detail_retains_parent_scope(owned_db, scope):
    db, parent = owned_db
    attachment = _attachment(db, parent, "ATTACH01")
    _annotation(db, attachment, "ANNOT001")
    with sqlite3.connect(db) as conn:
        if scope == "deleted":
            conn.execute("INSERT INTO deletedItems(itemID) VALUES (?)", (parent,))
        elif scope == "group":
            conn.execute("INSERT INTO libraries(libraryID, type) VALUES (4, 'group')")
            conn.execute("UPDATE items SET libraryID = 4")

    detail = _detail_without_writes(db, "MISSING1" if scope == "missing" else "PARENT01")

    assert detail is None


def test_annotation_schema_error_propagates_instead_of_empty(owned_db):
    db, parent = owned_db
    attachment = _attachment(db, parent, "ATTACH01")
    _annotation(db, attachment, "ANNOT001")
    with sqlite3.connect(db) as conn:
        conn.execute("DROP TABLE itemAnnotations")
    before = hashlib.sha256(db.read_bytes()).hexdigest()

    with pytest.raises(ZoteroReadError, match="itemAnnotations") as raised:
        ZoteroReader(db.parent).get_item_detail("PARENT01")

    assert raised.value.expected_io is False
    assert isinstance(raised.value.__cause__, sqlite3.OperationalError)
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before
