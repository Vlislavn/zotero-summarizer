"""Related review context uses current local intent, never generated-note interest."""
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.test_offline_sync import _db, _mutation
from tests.test_review_note_identity import STABLE, _feed, _materialize
from zotero_summarizer.api.routes import library
from zotero_summarizer.services.library import related
from zotero_summarizer.services.library._related_context import collect_comments
from zotero_summarizer.services.zotero._notes import build_user_note_html
from zotero_summarizer.storage import repositories as db
from zotero_summarizer.storage.library_context import read_context


class Reader:
    def __init__(self, items):
        self.items = {item['item_key']: item for item in items}

    def get_all_items(self, **kwargs):
        return {'items': list(self.items.values())}

    def get_item_detail(self, key):
        return self.items.get(key)


def setup_related(tmp_path, monkeypatch):
    path = _db(tmp_path)
    seed = {'item_key': 'PAPER001', 'title': 'Graph retrieval', 'abstract': 'Graph retrieval with bounded memory.'}
    candidate = {'item_key': 'PAPER002', 'title': 'Graph retrieval revisited',
                 'abstract': 'Graph retrieval with sparse indexes.', 'tags': ['read'], 'notes': [], 'annotations': []}
    reader = Reader([seed, candidate])
    monkeypatch.setattr(related, 'settings', lambda: SimpleNamespace(triage_db_path=path))
    monkeypatch.setattr(related, 'get_library_reader', lambda: reader)
    monkeypatch.setattr(related, 'resolve_reader_for_key', lambda key: reader)
    monkeypatch.setattr(related, '_retrieval_identity', lambda: {'encoder': 'synthetic-v1'})
    calls = []

    def rank(query, keys, **kwargs):
        calls.append((query, keys))
        return keys, dict.fromkeys(keys, .7), {'mode': 'hybrid_reranked', 'reranked': True}

    monkeypatch.setattr(related, 'hybrid_search', rank)
    return path, reader, calls


def test_http_related_includes_read_paper_and_literal_two_sided_evidence(tmp_path, monkeypatch):
    path, reader, calls = setup_related(tmp_path, monkeypatch)
    db.upsert_review_note(path, 'PAPER002', 'Не использовать: слишком дорого. Not useful for my goal.')
    app = FastAPI()
    app.include_router(library.router)

    response = TestClient(app).get('/api/library/related/PAPER001')

    assert response.status_code == 200
    result = response.json()
    assert result['saved_seed'] is True
    assert [row['item_key'] for row in result['items']] == ['PAPER002']
    row = result['items'][0]
    assert row['relationship']['kind'] == 'metadata_overlap'
    for side in ('seed', 'candidate'):
        evidence = row['evidence'][side]
        original = reader.items[evidence['item_key']][evidence['field']]
        assert original[evidence['start']:evidence['end']] == evidence['excerpt']
    assert row['comments']['records'][0]['text'].startswith('Не использовать')
    assert row['comments']['used_for_ranking'] is False
    assert calls[0][1] == ['PAPER002']
    assert 'дорого' not in calls[0][0]


def test_same_count_edit_and_delete_invalidate_full_content_context(tmp_path):
    path = _db(tmp_path)
    _feed(path)
    _materialize(path)
    db.upsert_review_note(path, STABLE, 'no')
    first = read_context(path, ['PAPER001'])['PAPER001']
    db.upsert_review_note(path, 'PAPER001', 'да')
    edited = read_context(path, [STABLE])[STABLE]
    assert first['manual']['value'] == 'no'
    assert edited['manual']['value'] == 'да'
    assert edited['manual']['revision'] > first['manual']['revision']
    revision = edited['manual']['revision']
    db.apply_sync_mutation(path, _mutation('PAPER001', 'review_note', None, revision, operation='delete'))
    deleted = read_context(path, [STABLE])[STABLE]
    assert deleted['manual']['value'] is None
    assert deleted['manual']['revision'] > revision


def test_comments_preserve_authorship_negative_unicode_and_exclude_owned_mirrors(tmp_path):
    path = _db(tmp_path)
    db.upsert_review_note(path, 'PAPER001', 'нет')
    context = read_context(path, ['PAPER001'])['PAPER001']
    detail = {'notes': [
        {'note_key': 'MIRROR', 'note': build_user_note_html('old deleted mirror')},
        {'note_key': 'AI', 'note': '<!-- zs:note_type=digest;version=1 --><p>generated</p>'},
        {'note_key': 'NATIVE', 'note': '<p>Не нравится</p>', 'date_modified': 'same-clock'},
    ], 'annotations': [{'annotation_key': 'ANN', 'attachment_key': 'PDF', 'comment': 'no',
                         'text': 'highlight is not a comment', 'date_modified': 'clock', 'page_label': '2'}]}

    result = collect_comments('PAPER001', detail, context)

    assert [row['text'] for row in result['records']] == ['нет', 'Не нравится', 'no']
    assert [row['authorship'] for row in result['records']] == ['user', 'uncertain', 'uncertain']
    assert result['records'][-1]['paper_excerpt'] == 'highlight is not a comment'
    assert result['used_for_ranking'] is False
    assert result['used_context_ids'] == []
    assert result['excluded_owned_notes'] == 2


def test_unavailable_is_not_empty_and_no_unrelated_fallback(tmp_path, monkeypatch):
    path, reader, calls = setup_related(tmp_path, monkeypatch)
    monkeypatch.setattr(related, 'hybrid_search', lambda *a, **kw: ([], {}, {
        'mode': 'corpus_off', 'semantic_unavailable': True}))
    result = related.related_papers('PAPER001')
    assert result['status'] == 'unavailable'
    assert result['items'] == []
    assert collect_comments('PAPER001', None, None)['availability'] == 'unavailable'
    assert collect_comments('PAPER001', {'notes': [], 'annotations': []}, {
        'aliases': ['PAPER001'], 'manual': {'value': None, 'revision': 0}, 'verdict': None,
    })['availability'] == 'absent'


def test_seed_aliases_excluded_and_limit_is_three(tmp_path, monkeypatch):
    path, reader, calls = setup_related(tmp_path, monkeypatch)
    _feed(path)
    _materialize(path)
    reader.items[STABLE] = {**reader.items['PAPER001'], 'item_key': STABLE}
    for index in range(3, 7):
        key = f'PAPER00{index}'
        reader.items[key] = {**reader.items['PAPER002'], 'item_key': key, 'doi': f'10.1/{index}'}
    result = related.related_papers('PAPER001')
    assert len(result['items']) == 3
    assert STABLE not in calls[0][1]


def test_current_human_verdict_only_not_generated_or_deleted_intent(tmp_path):
    path = _db(tmp_path)
    _feed(path)
    _materialize(path)
    for source, comment in [('auto_quality', 'generated criticism'), ('user', 'нет: слабые данные')]:
        db.insert_or_update_label_verdict(path, item_key=STABLE, original_derived_priority='could_read',
                                         user_priority='dont_read', comment=comment, source=source)
        context = read_context(path, ['PAPER001'])['PAPER001']
        result = collect_comments('PAPER001', {'notes': [], 'annotations': []}, context)
        assert [record['text'] for record in result['records']] == ([comment] if source == 'user' else [])
    revision = context['verdict']['revision']
    db.apply_sync_mutation(path, _mutation(STABLE, 'verdict', None, revision, operation='delete'))
    assert read_context(path, ['PAPER001'])['PAPER001']['verdict'] is None


def test_identifiers_reuse_normalized_public_doi_arxiv_versions():
    assert related.identifiers({'url': 'https://arxiv.org/pdf/2401.01234v2.pdf'}) == {'arxiv:2401.01234'}
    assert related.identifiers({'url': 'https://doi.org/10.1/ABC'}) == {'doi:10.1/abc'}
    assert related.identifiers({'url': 'https://example.org/arxiv/2401.01234'}) == set()


def test_duplicate_saved_versions_are_not_three_recommendations(tmp_path, monkeypatch):
    path, reader, calls = setup_related(tmp_path, monkeypatch)
    reader.items['PAPER002']['doi'] = '10.1/saved'
    reader.items['PAPER003'] = {**reader.items['PAPER002'], 'item_key': 'PAPER003', 'doi': 'https://doi.org/10.1/SAVED'}
    assert len(related.related_papers('PAPER001')['items']) == 1


def test_content_fingerprint_changes_without_note_count_or_timestamp_change(tmp_path, monkeypatch):
    path, reader, calls = setup_related(tmp_path, monkeypatch)
    detail = reader.items['PAPER002']
    detail['notes'] = [{'note_key': 'N', 'note': '<p>old</p>', 'date_modified': 'same'}]
    before = related.discovery_context('PAPER001')['fingerprint']
    detail['notes'][0]['note'] = '<p>new</p>'
    assert related.discovery_context('PAPER001')['fingerprint'] != before
    detail['notes'] = []
    assert related.discovery_context('PAPER001')['fingerprint'] != before


def test_positive_verdict_does_not_qualify_mixed_negative_comment_intent(tmp_path, monkeypatch):
    path, reader, calls = setup_related(tmp_path, monkeypatch)
    for index, priority in [(2, 'should_read'), (3, 'dont_read'), (4, 'could_read')]:
        key = f'PAPER00{index}'
        reader.items[key] = {**reader.items['PAPER002'], 'item_key': key}
        db.insert_or_update_label_verdict(path, item_key=key, original_derived_priority='could_read',
                                         user_priority=priority, comment='Не применять. Read only to understand why it fails.')
    db.upsert_review_note(path, 'PAPER002', 'Ambiguous manual text must not become an interest query.')
    reader.items['PAPER002']['notes'] = [{'note_key': 'N', 'note': '<p>uncertain authorship</p>'}]

    result = related.related_papers('PAPER001')

    assert [row['item_key'] for row in result['items']] == ['PAPER002', 'PAPER003', 'PAPER004']
    assert result['comments_in_ranking'] is False
    assert result['comment_ranking_qualification'] == 'unavailable_semantic_intent_unqualified'
    assert all(row['comments']['used_for_ranking'] is False and row['comments']['used_context_ids'] == []
               for row in result['items'])
    assert all('Не применять' not in query and 'Ambiguous manual text' not in query for query, _ in calls)
    assert result['items'][0]['comments']['records'][1]['text'] == 'Не применять. Read only to understand why it fails.'
