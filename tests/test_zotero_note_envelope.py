"""The SQLite adapter must write Zotero's native note envelope, not raw fragments."""
import sqlite3

import pytest

from tests._zotero_fixtures import add_library_item, build_zotero_db
from zotero_summarizer.integrations.zotero_write import ZoteroWriter

PREFIX = '<div class="zotero-note znv1">'


def _change(identifier, kind, html):
    payload = {'note_html': html}
    if kind == 'upsert_note':
        payload['marker'] = 'zs:note_type=triage'
    return {'id': identifier, 'item_key': 'ITEM0001', 'change_type': kind, 'payload_json': payload}


def _notes(db):
    with sqlite3.connect(db) as conn:
        return dict(conn.execute('SELECT itemID, note FROM itemNotes'))


@pytest.mark.parametrize('kind', ['add_note', 'upsert_note'])
@pytest.mark.parametrize('body', [
    '<!-- zs:note_type=triage --><h2>Methods</h2><p>H&amp;E with <strong>controls</strong>.</p>',
    '<p>zs:note_type=triage;version=3</p><ul><li>First</li><li>Second</li></ul>',
])
def test_sqlite_notes_have_native_envelope_and_preserve_inner_html(tmp_path, kind, body):
    db = build_zotero_db(tmp_path / 'zotero')
    add_library_item(db, item_key='ITEM0001', title='Title')
    writer = ZoteroWriter(db.parent)
    for identifier in (1, 2):
        assert writer.apply_changes([_change(identifier, kind, body)], create_backup=False)['failed'] == []
    notes = _notes(db)
    assert len(notes) == 1
    assert next(iter(notes.values())) == PREFIX + body + '</div>'


def test_upsert_updates_existing_owned_note_without_nesting_or_adopting_manual(tmp_path):
    db = build_zotero_db(tmp_path / 'zotero')
    add_library_item(db, item_key='ITEM0001', title='Title')
    writer = ZoteroWriter(db.parent)
    manual = PREFIX + '<p>Manual note</p></div>'
    old = PREFIX + '<!-- zs:note_type=triage;version=3 --><p>Old</p></div>'
    assert writer.apply_changes([_change(1, 'add_note', manual), _change(2, 'add_note', old)], create_backup=False)['failed'] == []
    before = _notes(db)
    owned_id = next(identifier for identifier, html in before.items() if 'zs:note_type=triage' in html)
    new = PREFIX + '<!-- zs:note_type=triage;version=3 --><h2>Findings</h2><p>New</p></div>'
    for identifier in (3, 4):
        assert writer.apply_changes([_change(identifier, 'upsert_note', new)], create_backup=False)['failed'] == []
    after = _notes(db)
    assert len(after) == 2
    assert after[owned_id] == new
    assert manual in after.values()


def test_replaying_legacy_bare_fragment_does_not_duplicate_or_rewrite_it(tmp_path):
    db = build_zotero_db(tmp_path / 'zotero')
    add_library_item(db, item_key='ITEM0001', title='Title')
    writer = ZoteroWriter(db.parent)
    body = '<h2>Legacy</h2><p>Existing content.</p>'
    assert writer.apply_changes([_change(1, 'add_note', body)], create_backup=False)['failed'] == []
    with sqlite3.connect(db) as conn:
        conn.execute('UPDATE itemNotes SET note = ?', (body,))
    before = _notes(db)
    assert writer.apply_changes([_change(2, 'add_note', body)], create_backup=False)['failed'] == []
    assert _notes(db) == before
