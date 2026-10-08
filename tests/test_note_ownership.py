"""Comment-discard wiring floor; not a native ProseMirror compatibility test."""
from html.parser import HTMLParser
import sqlite3
import re
from urllib.parse import quote, unquote

import pytest

from tests._zotero_fixtures import add_library_item, build_zotero_db
from tests.test_note_render import _summary
from zotero_summarizer.integrations.zotero_write import ZoteroWriter
from zotero_summarizer.models import PaperDigest
from zotero_summarizer.services.zotero._notes import (
    build_digest_note_html, build_triage_note_html,
    build_user_note_html, build_verdict_note_html,
)


class _TextOnly(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def _text(note):
    parser = _TextOnly()
    parser.feed(note)
    return ''.join(parser.parts)


def _render(kind):
    if kind == 'triage':
        return build_triage_note_html('Title', _summary(), run_id='run<&"')
    if kind == 'verdict':
        return build_verdict_note_html('must_read', 'Decision')
    if kind == 'digest':
        return build_digest_note_html(PaperDigest(tldr='Digest', key_strength='Strength', key_weakness='Weakness'))
    return build_user_note_html('Thoughts')


@pytest.mark.parametrize('kind', ['triage', 'verdict', 'digest', 'user_note'])
def test_ownership_text_survives_comment_discard_and_writer_finds_same_note(tmp_path, kind):
    marker = f'zs:note_type={kind}'
    rendered = _render(kind)
    text = _text(rendered)
    assert marker in text
    assert ('version=3' if kind == 'triage' else 'version=1') in text
    if kind == 'triage':
        assert 'run_id=run%3C%26%22' in text
        assert 'run_id=run%3C%26%22' in rendered
    db = build_zotero_db(tmp_path / 'zotero')
    add_library_item(db, item_key='ITEM0001', title='Title')
    writer = ZoteroWriter(db.parent)
    manual = '<h2>My notes</h2><p>Manual text, not owned.</p>'
    result = writer.apply_changes([
        {'id': 1, 'item_key': 'ITEM0001', 'change_type': 'add_note',
         'payload_json': {'note_html': manual}},
        {'id': 2, 'item_key': 'ITEM0001', 'change_type': 'add_note',
         'payload_json': {'note_html': re.sub(r'<!--.*?-->', '', rendered)}},
    ], create_backup=False)
    assert result['failed'] == []
    with sqlite3.connect(db) as conn:
        owned_id = conn.execute('SELECT itemID FROM itemNotes WHERE instr(note, ?) > 0', (marker,)).fetchone()[0]
    result = writer.apply_changes([
        {'id': 3, 'item_key': 'ITEM0001', 'change_type': 'upsert_note',
         'payload_json': {'marker': marker, 'note_html': rendered}},
    ], create_backup=False)
    assert result['failed'] == []
    with sqlite3.connect(db) as conn:
        notes = dict(conn.execute('SELECT itemID, note FROM itemNotes'))
    assert len(notes) == 2
    assert notes[owned_id] == '<div class="zotero-note znv1">' + rendered + '</div>'
    assert '<div class="zotero-note znv1">' + manual + '</div>' in notes.values()
    assert marker not in _text(manual)


def test_disabled_provenance_has_no_ownership_text():
    note = build_triage_note_html('Title', _summary(), include_provenance=False, run_id='private-run')
    assert 'zs:note_type=' not in note
    assert 'private-run' not in note


def test_empty_user_note_keeps_text_ownership():
    assert 'zs:note_type=user_note;version=1' in _text(build_user_note_html(''))


@pytest.mark.parametrize('run_id', ['bad-->run<!--', '<script>alert("x")</script>&', 'a--b', 'Привет', 'plain-readable_123', 'zs:note_type=verdict', '%3B'])
def test_original_run_id_is_escaped_plain_text(run_id):
    note = build_triage_note_html('Title', _summary(), run_id=run_id)
    encoded = quote(run_id, safe='').replace('--', '%2D%2D')
    assert f'run_id={encoded}' in _text(note)
    assert f'run_id={encoded}' in note.split('-->', 1)[0]
    assert unquote(encoded) == run_id
    assert note.count('zs:note_type=') == 2
    assert '--' not in note.removeprefix('<!--').split('-->', 1)[0]
    assert '<script>' not in note.split('-->', 1)[1]
    assert note.count('<!--') == 1
    assert note.count('-->') == 1

@pytest.mark.parametrize('discard_comment', [False, True])
def test_run_metadata_cannot_redirect_verdict_upsert(tmp_path, discard_comment):
    triage = build_triage_note_html('Title', _summary(), run_id='run;zs:note_type=verdict')
    if discard_comment:
        triage = re.sub(r'<!--.*?-->', '', triage)
    db = build_zotero_db(tmp_path / 'zotero')
    add_library_item(db, item_key='ITEM0001', title='Title')
    writer = ZoteroWriter(db.parent)
    manual = '<p>Manual note</p>'
    def change(identifier, kind, payload):
        return {'id': identifier, 'item_key': 'ITEM0001', 'change_type': kind, 'payload_json': payload}
    assert writer.apply_changes([
        change(1, 'add_note', {'note_html': manual}),
        change(2, 'add_note', {'note_html': triage}),
    ], create_backup=False)['failed'] == []
    with sqlite3.connect(db) as conn:
        before = dict(conn.execute('SELECT itemID, note FROM itemNotes'))
    verdict = build_verdict_note_html('must_read', 'Decision')
    assert writer.apply_changes([
        change(3, 'upsert_note', {'marker': 'zs:note_type=verdict', 'note_html': verdict}),
    ], create_backup=False)['failed'] == []
    with sqlite3.connect(db) as conn:
        after = dict(conn.execute('SELECT itemID, note FROM itemNotes'))
    assert all(after[identifier] == note for identifier, note in before.items())
    assert len(after) == 3
    assert '<div class="zotero-note znv1">' + verdict + '</div>' in after.values()

@pytest.mark.parametrize('kind', ['triage', 'verdict', 'digest', 'user_note'])
def test_serialized_footer_identifies_only_actual_kind(kind):
    text = _text(_render(kind))
    assert text.count('zs:note_type=') == 1
    assert f'zs:note_type={kind}' in text


def test_readable_legacy_comment_run_id_still_upserts(tmp_path):
    db = build_zotero_db(tmp_path / 'zotero')
    add_library_item(db, item_key='ITEM0001', title='Title')
    writer = ZoteroWriter(db.parent)
    legacy = '<!-- zs:note_type=triage;version=3;run_id=abc-123 --><p>Legacy</p>'
    assert writer.apply_changes([
        {'id': 1, 'item_key': 'ITEM0001', 'change_type': 'add_note',
         'payload_json': {'note_html': legacy}},
    ], create_backup=False)['failed'] == []
    with sqlite3.connect(db) as conn:
        identifier = conn.execute('SELECT itemID FROM itemNotes').fetchone()[0]
    rendered = build_triage_note_html('Title', _summary(), run_id='abc-123')
    assert writer.apply_changes([
        {'id': 2, 'item_key': 'ITEM0001', 'change_type': 'upsert_note',
         'payload_json': {'marker': 'zs:note_type=triage', 'note_html': rendered}},
    ], create_backup=False)['failed'] == []
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT itemID, note FROM itemNotes').fetchall() == [
            (identifier, '<div class="zotero-note znv1">' + rendered + '</div>'),
        ]

@pytest.mark.parametrize('manual', [
    '<p>Paper mentions zs:note_type=triage in its methods.</p>',
    '<p>&lt;!-- zs:note_type=triage;version=3 --&gt;</p>',
    '<pre><code><!-- zs:note_type=triage;version=3 --></code></pre>',
])
def test_literal_marker_does_not_adopt_manual_note(tmp_path, manual):
    db = build_zotero_db(tmp_path / 'zotero')
    add_library_item(db, item_key='ITEM0001', title='Title')
    writer = ZoteroWriter(db.parent)
    def change(identifier, operation, html):
        return {'id': identifier, 'item_key': 'ITEM0001', 'change_type': operation,
                'payload_json': {'marker': 'zs:note_type=triage', 'note_html': html}}
    assert writer.apply_changes([change(1, 'add_note', manual)], create_backup=False)['failed'] == []
    with sqlite3.connect(db) as conn:
        before = dict(conn.execute('SELECT itemID, note FROM itemNotes'))
    for identifier in (2, 3):
        assert writer.apply_changes([change(identifier, 'upsert_note', _render('triage'))], create_backup=False)['failed'] == []
    with sqlite3.connect(db) as conn:
        after = dict(conn.execute('SELECT itemID, note FROM itemNotes'))
    assert len(after) == 2
    assert all(after[key] == value for key, value in before.items())

@pytest.mark.parametrize('footer,expected', [
    ('zs:note_type=verdict;version=1', False),
    ('zs:note_type=triage;version=3', False),
    ('zs:note_type=triage%3Bzs%3Anote_type%3Dverdict;version=3;generated_at=2026-10-07T19:26:17Z;source=feed-batch', False),
    ('zs:note_type=triage;version=3;version=3;generated_at=2026-10-07T19:26:17Z;source=feed-batch', False),
    ('zs:note_type=triage;version=3;generated_at=2026-10-07T19:26:17Z;source=custom%3Bsource', True),
])
def test_footer_is_authoritative_and_ambiguous_metadata_is_unowned(footer, expected):
    from zotero_summarizer.integrations._zotero_write_tags import _owns_note
    note = '<!-- zs:note_type=triage;version=3 --><p>Body zs:note_type=triage</p><p><em>' + footer + '</em></p>'
    assert _owns_note(note, 'zs:note_type=triage') is expected


def test_removing_native_footer_leaves_unowned():
    from zotero_summarizer.integrations._zotero_write_tags import _owns_note
    note = re.sub(r'<!--.*?-->', '', _render('triage'))
    note = note[:note.rfind('<p>')]
    assert not _owns_note(note, 'zs:note_type=triage')
