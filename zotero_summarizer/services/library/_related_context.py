"""Retain private source identity and complete RU/EN text, not inferred interests."""
import hashlib
from html.parser import HTMLParser
import json
import sqlite3

from zotero_summarizer.integrations._zotero_write_tags import _owns_note
from zotero_summarizer.integrations.zotero_read import ZoteroReadError
from zotero_summarizer.services.library.app_library_reader import AppLibraryReader
from zotero_summarizer.models.zotero_actions import VERDICT_NOTE_MARKER
from zotero_summarizer.services.zotero._notes import DIGEST_NOTE_MARKER, USER_NOTE_MARKER
from zotero_summarizer.storage.library_context import read_context


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _expected_io(exc):
    code = getattr(exc, "sqlite_errorcode", 0) & 255
    return isinstance(exc, OSError) or code in {
        sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED, sqlite3.SQLITE_IOERR,
        sqlite3.SQLITE_CANTOPEN, sqlite3.SQLITE_READONLY,
    }


def app_context(db_path, keys):
    try:
        return read_context(db_path, keys), "observed"
    except sqlite3.Error as exc:
        if not _expected_io(exc):
            raise
        return {}, "failed"
    except OSError:
        return {}, "failed"


def native_detail(reader, key):
    try:
        detail = reader.get_item_detail(key)
    except ZoteroReadError as exc:
        if not _expected_io(exc.__cause__):
            raise
        return None, "failed"
    except sqlite3.Error as exc:
        if not _expected_io(exc):
            raise
        return None, "failed"
    except OSError:
        return None, "failed"
    if detail is None:
        return None, "unknown"
    return detail, "unavailable" if isinstance(reader, AppLibraryReader) else "observed"


class _NoteText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    def handle_starttag(self, tag, attrs):
        if tag in {"p", "br", "div", "li", "h1", "h2", "h3"}:
            self.parts.append("\n")


def _record(key, source, ident, text, revision, authorship):
    return {"id": f"{key}:{source}:{ident}", "item_key": key, "source": source,
            "source_id": ident, "text": text, "revision": revision, "authorship": authorship}


def collect_comments(key, detail, context, app_state, native_state):
    records, excluded = [], []
    native_key = (detail or {}).get("item_key") or key
    if context is not None:
        manual, verdict = context["manual"], context["verdict"]
        if manual.get("value"):
            records.append(_record(manual["item_key"], "app_note", manual["item_key"],
                                   manual["value"], manual["revision"], "user"))
        if verdict and verdict.get("source") == "user" and verdict.get("value") and verdict.get("comment"):
            records.append(_record(verdict["item_key"], "human_verdict", verdict["item_key"],
                                   verdict["comment"], verdict["revision"], "user"))
    for note in (detail or {}).get("notes", []):
        markup = note.get("note") or ""
        if any(_owns_note(markup, marker) for marker in (
                DIGEST_NOTE_MARKER, USER_NOTE_MARKER, VERDICT_NOTE_MARKER, "zs:note_type=triage")):
            excluded.append(note.get("note_key"))
            continue
        parser = _NoteText()
        parser.feed(markup)
        parser.close()
        text = "".join(parser.parts).strip()
        if text:
            records.append(_record(native_key, "native_note", note["note_key"], text,
                                   note.get("date_modified"), "uncertain"))
    for annotation in (detail or {}).get("annotations", []):
        if annotation.get("comment"):
            record = _record(native_key, "annotation_comment", annotation["annotation_key"],
                             annotation["comment"], annotation.get("date_modified"), "uncertain")
            records.append({**record, "attachment_key": annotation.get("attachment_key"),
                            "paper_excerpt": annotation.get("text", ""), "page_label": annotation.get("page_label")})
    states = {app_state, native_state}
    availability = ("available" if records else "empty") if states == {"observed"} else "partial" if records else "unavailable"
    return {"availability": availability, "app_state": app_state, "native_state": native_state,
            "records": records, "excluded_owned_notes": excluded, "used_context_ids": []}
