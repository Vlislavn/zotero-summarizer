"""Accepted production verification -> bound cache -> internal read-only export."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import os
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from test_digest_verification import PAPER, _Verifier, _digest
from test_review_source_boundary import _Mapper
from zotero_summarizer.api.errors import APIError, install_error_handlers
from zotero_summarizer.api.routes import library
from zotero_summarizer.runtime import AppContext, set_context
from zotero_summarizer.services.library import _review_cache, deep_review
from zotero_summarizer.services.setup.bootstrap import _default_goals_config
from zotero_summarizer.services.zotero import generated_notes
from zotero_summarizer.settings import Settings

KEY = 'feed:d:' + 'a' * 64
URL = f'/api/library/deep-review/{KEY}/audit'


class ScriptedClient(_Verifier):
    def __init__(self, *, correction=False):
        super().__init__()
        self.generated = 0
        self.correction = correction

    def pydantic_prompt(self, *, pydantic_model, **kwargs):
        from zotero_summarizer.models import PaperDigest
        if pydantic_model is not PaperDigest:
            return super().pydantic_prompt(pydantic_model=pydantic_model, **kwargs)
        self.generated += 1
        if self.correction and self.generated == 1:
            return _digest('The paper reports 999% accuracy.')
        return _digest().model_copy(update={'key_findings': ['Held-out data were used.']})


@pytest.fixture
def audit_env(tmp_path, monkeypatch):
    settings = replace(Settings.load(project_root=tmp_path), pdf_root=tmp_path)
    config = _default_goals_config()
    context = AppContext(settings=settings)
    context.state.app_state = SimpleNamespace(config=config)
    set_context(context)
    monkeypatch.setattr(_review_cache, '_cache_path', lambda: tmp_path / 'deep_reviews.json')
    monkeypatch.setattr(deep_review._deep_review_layers, 'extra_layers', lambda ctx: (None,) * 5)
    monkeypatch.setattr(deep_review, '_try_rebuild_render', lambda key: None)
    with deep_review._LOCK:
        deep_review._JOBS.clear()
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(library.router)
    return SimpleNamespace(settings=settings, config=config, app=app, root=tmp_path, context=context)


def publish(env, *, correction=False, strategy='rank', mapper=None, source=PAPER):
    env.config.quality_review.chunk_strategy = strategy
    path = env.root / 'source.pdf'
    path.write_bytes(b'Synthetic source bytes, not a private PDF')
    generator = ScriptedClient(correction=correction)
    reader = SimpleNamespace(get_item_detail=lambda key: {'title': 'Synthetic paper'})
    extractor = SimpleNamespace(extract_text=lambda path: source)
    context = dict(reader=reader, config=env.config, extractor=extractor, llm=generator,
                   llm_map=mapper or generator, quality_enabled=True)
    deep_review._review_worker({'item_key': KEY, 'pdf_path': str(path)}, context, '')
    assert deep_review.status(KEY)['status'] == 'ready'
    entry = _review_cache._read_all(readonly=True)[KEY]
    return entry, generator, path


def http_get(env, url=URL, **kwargs):
    with TestClient(env.app, base_url='http://localhost', client=('127.0.0.1', 50000)) as client:
        return client.get(url, **kwargs)


def test_actual_review_correction_cache_internal_export_roundtrip(audit_env, monkeypatch):
    entry, generator, path = publish(audit_env, correction=True)
    before = _review_cache._cache_path().read_bytes()
    source_before = path.read_bytes()
    def forbidden(*args, **kwargs):
        raise AssertionError('Export must not resolve clients, extract, acquire, write or invoke native effects')
    monkeypatch.setattr(deep_review, '_build_ctx', forbidden)
    monkeypatch.setattr(_review_cache, '_write_one', forbidden)
    monkeypatch.setattr(_review_cache, '_quarantine_corrupt_cache', forbidden)
    monkeypatch.setattr(deep_review, '_try_rebuild_render', forbidden)
    import keyring
    import sqlite3
    from zotero_summarizer.services.llm import factory
    from zotero_summarizer.services.library import _pdf_acquire
    from zotero_summarizer.services.zotero import zotero
    monkeypatch.setattr(factory, 'resolve_stage_client', forbidden)
    monkeypatch.setattr(keyring, 'get_password', forbidden)
    monkeypatch.setattr(sqlite3, 'connect', forbidden)
    monkeypatch.setattr(_pdf_acquire, 'acquire_for_item', forbidden)
    monkeypatch.setattr(zotero, 'get_library_reader', forbidden)
    monkeypatch.setattr(zotero, 'resolve_reader_for_key', forbidden)
    monkeypatch.setattr(generated_notes, 'stage_current_digest', forbidden)

    payload = _review_cache.export_review_audit(KEY)
    assert payload['item_key'] == KEY and payload['digest'] == entry['digest']
    assert payload['audit_status'] == 'available' and payload['reason'] == ''
    audit = payload['verification_audit']
    assert audit == entry['verification_audit']
    assert payload['retained_evidence'] == {'scope': 'retained_source_only',
        'current_item_binding': 'not_established', 'verification_audit': audit}
    assert [row['outcome'] for row in audit['lineage']] == ['support_not_established', 'accepted']
    assert audit['lineage'][0]['failure_kind'] == 'numeric_preflight'
    assert generator.generated == 2 and generator.calls == 1
    assert {row['field'] for row in audit['claims']} == {'tldr', 'key_findings'}
    assert audit['claims'][1]['path'] == 'key_findings[0]'
    assert all(row['numeric_outcome'] == 'passed_existing_literal_preflight' for row in audit['claims'])
    assert audit['source']['scope'] == 'complete_extracted_text'
    assert audit['source']['paper_completeness'] == 'not_established'
    assert audit['source']['original_text_sha256'] == hashlib.sha256(PAPER.encode()).hexdigest()
    assert audit['contradiction_adjudication'] == 'not_performed'
    passage = audit['passages'][0]
    assert passage['text'] == PAPER and passage['pdf_page'] is None
    assert passage['page_mapping_reason'] == 'text_extraction_has_no_page_mapping'
    fragment = passage['original_fragments'][0]
    assert fragment['original_span'] == {'start': 0, 'end': len(PAPER)}
    assert fragment['text'] == PAPER
    assert _review_cache._cache_path().read_bytes() == before and path.read_bytes() == source_before
    assert sorted(p.name for p in audit_env.root.iterdir()) == ['deep_reviews.json', 'source.pdf']


def test_map_fallback_is_retained_through_real_publication_and_export(audit_env):
    class MalformedMapper(_Mapper):
        def pydantic_prompt(self, **kwargs):
            self.calls += 1
            return {'checks': []}

    mapper = MalformedMapper('Generated notes are not original evidence.')
    entry, generator, _ = publish(audit_env, strategy='map_reduce', mapper=mapper)
    payload = _review_cache.export_review_audit(KEY)
    audit = payload['verification_audit']
    assert audit == entry['verification_audit']
    assert audit['lineage'][0]['verifier_route'] == 'generator_fallback'
    assert audit['lineage'][0]['unavailable_routes'] == ['primary']
    assert mapper.calls == 2 and generator.generated == generator.calls == 1
    assert payload['digest']['basis'] == 'map_reduce'
    assert audit['verified_digest']['basis'] == 'full_text'
    assert 'basis' in audit['post_verification_changes']
    assert all(row['matches_cached_field'] for row in audit['claims'])
    assert audit['passages'][0]['text'] == PAPER


@pytest.mark.parametrize('strategy', ['rank', 'prefix'])
def test_partial_selection_reports_actual_original_fragments_and_unknown_completeness(audit_env, strategy):
    source = 'Background prose. ' * 250 + 'Methodology results findings evaluation. ' * 100
    audit_env.config.quality_review.max_text_chars = 700
    publish(audit_env, strategy=strategy, source=source)
    payload = _review_cache.export_review_audit(KEY)
    assert payload['audit_status'] == 'available'
    audit = payload['verification_audit']
    assert audit['source']['original_characters'] == len(source.strip())
    assert audit['source']['selected_characters'] <= 700
    assert audit['source']['scope'] == 'selected_original'
    assert audit['source']['paper_completeness'] == 'not_established'
    for passage in audit['passages']:
        assert passage['pdf_page'] is None
        for fragment in passage['original_fragments']:
            origin = fragment['original_span']
            if origin is not None:
                assert source[origin['start']:origin['end']] == fragment['text']
    if strategy == 'rank':
        assert audit['passages'][0]['original_fragments'][0]['original_span']['start'] > 0


def test_repeated_text_keeps_occurrence_coordinates_not_first_find(audit_env):
    class SecondPassage(_Mapper):
        def _checks(self, prompt):
            return [{**row, 'evidence': [1]} for row in super()._checks(prompt)]

    source = ('Repeated source words demonstrate exact occurrence identity in text. ' * 220)
    audit_env.config.quality_review.max_text_chars = 2800
    publish(audit_env, mapper=SecondPassage('unused'), source=source)
    audit = _review_cache.export_review_audit(KEY)['verification_audit']
    assert audit['passages'][0]['id'] == 1
    exact = [fragment for passage in audit['passages'] for fragment in passage['original_fragments']
             if fragment['original_span'] is not None]
    assert all(source[f['original_span']['start']:f['original_span']['end']] == f['text'] for f in exact)
    assert any(source.find(f['text']) != f['original_span']['start'] for f in exact)


@pytest.mark.parametrize('drift', ['digest', 'review_identity', 'source', 'source_same_stat',
                                   'extraction', 'verifier', 'configuration'])
def test_export_drift_invalidates_current_evidence_without_changing_stored_bytes(audit_env, monkeypatch, drift):
    from zotero_summarizer.services.library import _digest_audit
    entry, _, path = publish(audit_env)
    expected = 'cached_artifact_changed'
    if drift in {'digest', 'review_identity'}:
        changed = deepcopy(entry)
        if drift == 'digest':
            changed['digest']['tldr'] = 'Different cached conclusion.'
        else:
            changed['review_identity']['source_sha256'] = 'b' * 64
        _review_cache._write_one(KEY, changed)
    elif drift.startswith('source'):
        stat = path.stat()
        path.write_bytes(b'x' * stat.st_size)
        if drift == 'source_same_stat':
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        expected = 'source_bytes_changed'
    elif drift in {'extraction', 'verifier'}:
        hashes = _digest_audit.contract_hashes()
        hashes[drift + '_contract_sha256'] = 'c' * 64
        monkeypatch.setattr('zotero_summarizer.services.library._digest_audit_export.contract_hashes', lambda: hashes)
        expected = 'audit_contract_changed'
    else:
        audit_env.config.research_goals.append('Different reader preference')
        expected = 'review_configuration_changed'
    before = _review_cache._cache_path().read_bytes()
    result = _review_cache.export_review_audit(KEY)
    assert result['audit_status'] == 'stale' and result['reason'] == expected
    assert result['verification_audit'] is None and result['retained_evidence'] is None
    assert not _review_cache.review_is_current(_review_cache._read_all(readonly=True)[KEY], KEY)
    assert _review_cache._cache_path().read_bytes() == before


@pytest.mark.parametrize('legacy', [True, False])
def test_legacy_and_missing_source_remain_unavailable_not_scientific_negatives(audit_env, legacy):
    entry, _, path = publish(audit_env)
    if legacy:
        entry.pop('verification_audit')
        _review_cache._write_one(KEY, entry)
    else:
        path.unlink()
    result = _review_cache.export_review_audit(KEY)
    assert result['audit_status'] == 'unavailable' and result['verification_audit'] is None
    assert result['retained_evidence'] is None
    assert result['reason'] == ('legacy_audit_unavailable' if legacy else 'source_unavailable')
    assert result['digest'] == entry['digest']


@pytest.mark.parametrize('damage', ['contract', 'claim_path', 'fragment', 'schema', 'page', 'lineage',
                                    'missing_claims', 'duplicate_evidence'])
def test_structurally_malformed_receipts_are_errors_not_negative_labels(audit_env, damage):
    from zotero_summarizer.services.library._digest_audit import canonical_hash, payload_hash
    entry, _, _ = publish(audit_env)
    audit = entry['verification_audit']
    if damage == 'contract':
        audit['contract'] = 'unknown-schema'
    elif damage == 'claim_path':
        audit['claims'][0]['path'] = 'invented'
    elif damage == 'fragment':
        audit['passages'][0]['original_fragments'][0]['original_span']['start'] = 1
    elif damage == 'schema':
        audit['verified_digest']['read_decision'] = 'invalid'
        identity = canonical_hash(audit['verified_digest'])
        audit['identity']['verified_digest_sha256'] = audit['lineage'][-1]['digest_sha256'] = identity
    elif damage == 'page':
        audit['passages'][0]['pdf_page'] = 1
    elif damage == 'missing_claims':
        audit.pop('claims')
    elif damage == 'duplicate_evidence':
        audit['claims'][0]['evidence'] = [0, 0]
    else:
        audit['lineage'][-1]['outcome'] = 'contradiction'
    audit['identity']['audit_sha256'] = payload_hash(audit)
    _review_cache._write_one(KEY, entry)
    before = _review_cache._cache_path().read_bytes()
    with pytest.raises(APIError) as error:
        _review_cache.export_review_audit(KEY)
    assert (error.value.status_code, error.value.error) == (422, 'audit_invalid')
    assert _review_cache._cache_path().read_bytes() == before


@pytest.mark.parametrize('raw', [b'{', b'[]', b'{"reviews":[]}', b'\xff'])
def test_export_never_quarantines_or_repairs_malformed_cache(audit_env, raw):
    _review_cache._cache_path().write_bytes(raw)
    with pytest.raises(APIError) as error:
        _review_cache.export_review_audit(KEY)
    assert (error.value.status_code, error.value.error) == (422, 'audit_invalid')
    assert _review_cache._cache_path().read_bytes() == raw
    assert sorted(p.name for p in audit_env.root.iterdir()) == ['deep_reviews.json']


@pytest.mark.parametrize('key', ['short', ' ABCDEFGH', 'feed:d:' + 'z' * 64])
def test_export_requires_an_exact_valid_key(audit_env, key):
    with pytest.raises(APIError) as error:
        _review_cache.export_review_audit(key)
    assert (error.value.status_code, error.value.error) == (422, 'validation_error')
    assert not _review_cache._cache_path().exists()


def test_materialization_cache_copy_preserves_exact_receipt_without_native_action(audit_env):
    entry, _, _ = publish(audit_env)
    receipt = deepcopy(entry['verification_audit'])
    assert _review_cache.copy_review(KEY, 'ABCDEFGH')
    copied = _review_cache.export_review_audit('ABCDEFGH')
    assert copied['audit_status'] == 'available' and copied['item_key'] == 'ABCDEFGH'
    assert copied['verification_audit'] == receipt
    assert _review_cache._read_all(readonly=True)[KEY] == entry


def test_changed_source_during_generation_never_reaches_note_or_cache(audit_env, monkeypatch):
    entry, _, path = publish(audit_env)
    before = _review_cache._cache_path().read_bytes()

    class MutatingClient(ScriptedClient):
        def pydantic_prompt(self, **kwargs):
            path.write_bytes(b'Source replacement during generation')
            return super().pydantic_prompt(**kwargs)

    def forbidden(*args, **kwargs):
        pytest.fail('Changed source must stop before note publication')
    monkeypatch.setattr(generated_notes, 'stage_current_digest', forbidden)
    monkeypatch.setattr(deep_review, '_write_one', forbidden)
    reader = SimpleNamespace(get_item_detail=lambda key: {'title': 'Synthetic paper'})
    with pytest.raises(ValueError, match='source changed'):
        deep_review._review_one({'item_key': KEY, 'pdf_path': str(path)}, reader=reader,
                                extractor=SimpleNamespace(extract_text=lambda path: PAPER),
                                config=audit_env.config, llm=MutatingClient(), quality_enabled=True)
    assert _review_cache._cache_path().read_bytes() == before
    assert _review_cache._read_all(readonly=True)[KEY] == entry


def _publish_attached(env, monkeypatch):
    import sqlite3
    from tests._zotero_fixtures import add_library_item, build_zotero_db
    from zotero_summarizer.integrations.zotero_read import ZoteroReader

    db = build_zotero_db(env.root / 'synthetic-zotero')
    parent = add_library_item(db, item_key='ABCDEFGH', title='Synthetic source')
    path = env.root / 'attached.pdf'
    path.write_bytes(b'Synthetic attached source')
    with sqlite3.connect(db) as conn:
        child = conn.execute("INSERT INTO items(itemTypeID,libraryID,key) VALUES(3,1,'ATTACH01')").lastrowid
        conn.execute('INSERT INTO itemAttachments(itemID,parentItemID,contentType,path) VALUES(?,?,?,?)',
                     (child, parent, 'application/pdf', str(path)))
    reader = ZoteroReader(db.parent)
    env.context.state.zotero_reader = reader
    context = dict(reader=reader, config=env.config, extractor=SimpleNamespace(extract_text=lambda path: PAPER),
                   llm=ScriptedClient(), quality_enabled=True)
    deep_review._review_worker({'item_key': 'ABCDEFGH'}, context, '')
    assert deep_review.status('ABCDEFGH')['status'] == 'ready'
    return reader, db, path


@pytest.mark.parametrize('binding', ['unchanged', 'changed', 'missing_reader', 'unsafe_reader', 'reader_error'])
def test_native_export_retains_source_evidence_without_reader_access(audit_env, monkeypatch, binding):
    import sqlite3
    import keyring
    from zotero_summarizer.integrations.zotero_read import ZoteroReader
    from zotero_summarizer.services.llm import factory
    from zotero_summarizer.services.library import _pdf_acquire
    from zotero_summarizer.services.zotero import zotero

    reader, db, original_source = _publish_attached(audit_env, monkeypatch)
    if binding == 'changed':
        replacement = audit_env.root / 'replacement.pdf'
        replacement.write_bytes(b'Different attachment, original file still intact')
        with sqlite3.connect(db) as conn:
            conn.execute('UPDATE itemAttachments SET path=?', (str(replacement),))
    def forbidden(*args, **kwargs):
        raise AssertionError('No reader initialization, clients, credentials, acquisition or writes on export')
    if binding == 'missing_reader':
        audit_env.context.state.zotero_reader = None
    elif binding == 'unsafe_reader':
        audit_env.context.state.zotero_reader = SimpleNamespace(get_item_detail=forbidden)
    elif binding == 'reader_error':
        def unreadable(key):
            pytest.fail('Even a failing initialized reader must not be invoked during export')
        monkeypatch.setattr(reader, 'get_item_detail', unreadable)
    else:
        monkeypatch.setattr(reader, 'get_item_detail', forbidden)
    connect = sqlite3.connect
    def readonly_connect(database, **kwargs):
        assert kwargs.get('uri') is True and '?mode=ro' in database
        conn = connect(database, **kwargs)
        conn.set_authorizer(lambda operation, *args: sqlite3.SQLITE_DENY if operation in {
            sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE,
            sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_DROP_TABLE,
        } else sqlite3.SQLITE_OK)
        return conn
    monkeypatch.setattr(sqlite3, 'connect', readonly_connect)
    monkeypatch.setattr(ZoteroReader, '__init__', forbidden)
    monkeypatch.setattr(factory, 'resolve_stage_client', forbidden)
    monkeypatch.setattr(zotero, 'get_library_reader', forbidden)
    monkeypatch.setattr(zotero, 'resolve_reader_for_key', forbidden)
    monkeypatch.setattr(generated_notes, 'stage_current_digest', forbidden)
    monkeypatch.setattr(keyring, 'get_password', forbidden)
    monkeypatch.setattr(_pdf_acquire, 'acquire_for_item', forbidden)
    monkeypatch.setattr(_review_cache, '_write_one', forbidden)
    before = {str(p.relative_to(audit_env.root)): p.read_bytes() for p in audit_env.root.rglob('*') if p.is_file()}

    payload = _review_cache.export_review_audit('ABCDEFGH')
    assert (payload['audit_status'], payload['reason']) == ('unavailable', 'current_item_binding_unavailable')
    assert payload['verification_audit'] is None
    retained = payload['retained_evidence']
    assert retained['scope'] == 'retained_source_only' and retained['current_item_binding'] == 'not_established'
    assert retained['verification_audit'] == _review_cache._read_all(readonly=True)['ABCDEFGH']['verification_audit']
    assert original_source.read_bytes() == b'Synthetic attached source'
    after = {str(p.relative_to(audit_env.root)): p.read_bytes() for p in audit_env.root.rglob('*') if p.is_file()}
    assert after == before


@pytest.mark.parametrize('mutation', ['changed_attachment', 'missing_attachment'])
def test_current_cache_requires_actual_unchanged_initialized_attachment(audit_env, monkeypatch, mutation):
    import sqlite3

    _, db, original_source = _publish_attached(audit_env, monkeypatch)
    before = _review_cache._cache_path().read_bytes()
    def forbidden(*args, **kwargs):
        pytest.fail('Ordinary current-cache reuse must not regenerate or resolve clients')
    monkeypatch.setattr(ScriptedClient, 'pydantic_prompt', forbidden)
    monkeypatch.setattr(deep_review, '_build_ctx', forbidden)
    monkeypatch.setattr(deep_review, '_review_worker', forbidden)
    assert _review_cache.get_current_review('ABCDEFGH') is not None
    assert 'ABCDEFGH' in _review_cache.current_review_keys()
    assert _review_cache.export_review_audit('ABCDEFGH')['audit_status'] == 'unavailable'
    assert _review_cache.get_current_review('ABCDEFGH') is not None
    replacement = audit_env.root / 'new-attachment.pdf'
    replacement.write_bytes(b'Another synthetic source')
    with sqlite3.connect(db) as conn:
        if mutation == 'missing_attachment':
            conn.execute('DELETE FROM itemAttachments')
        else:
            conn.execute('UPDATE itemAttachments SET path=?', (str(replacement),))
    assert original_source.read_bytes() == b'Synthetic attached source'
    assert _review_cache.get_current_review('ABCDEFGH') is None
    assert 'ABCDEFGH' not in _review_cache.current_review_keys()
    assert _review_cache._cache_path().read_bytes() == before


def test_missing_key_is_not_found_without_cache_creation(audit_env):
    with pytest.raises(APIError) as error:
        _review_cache.export_review_audit(KEY)
    assert (error.value.status_code, error.value.error) == (404, 'not_found')
    assert list(audit_env.root.iterdir()) == []


def test_published_audit_has_no_http_surface_or_cache_read(audit_env, monkeypatch):
    publish(audit_env)
    before = _review_cache._cache_path().read_bytes()
    def forbidden(*args, **kwargs):
        pytest.fail('An absent HTTP surface cannot read the audit cache')
    monkeypatch.setattr(_review_cache, '_read_all', forbidden)
    response = http_get(audit_env)
    assert response.status_code == 404 and response.json() == {'detail': 'Not Found'}
    assert _review_cache._cache_path().read_bytes() == before


@pytest.mark.parametrize('host,peer,origin', [
    ('http://localhost', '127.0.0.1', None),
    ('http://example.org', '127.0.0.1', None),
    ('http://localhost', '192.0.2.10', None),
    ('http://localhost', '127.0.0.1', 'https://example.org'),
    ('http://localhost', '127.0.0.1', 'null'),
])
def test_audit_http_surface_is_absent_for_local_nonlocal_and_cross_origin(audit_env, monkeypatch, host, peer, origin):
    def forbidden(*args, **kwargs):
        pytest.fail('An absent HTTP surface cannot read the audit cache')
    monkeypatch.setattr(_review_cache, '_read_all', forbidden)
    headers = {} if origin is None else {'Origin': origin}
    with TestClient(audit_env.app, base_url=host, client=(peer, 50000)) as client:
        assert client.get(URL, headers=headers).status_code == 404
    assert list(audit_env.root.iterdir()) == []
