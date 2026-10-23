"""Native cache ownership must not inherit the export-only PDF root restriction."""
from dataclasses import replace
import sqlite3

import pytest

from test_verification_audit_export import audit_env, _publish_attached, get, publish  # noqa: F401
from zotero_summarizer.services.library import _review_cache, _review_identity


@pytest.mark.parametrize('mutation', ['changed_attachment', 'missing_attachment'])
def test_linked_attachment_outside_export_roots_keeps_only_unchanged_cache(audit_env, monkeypatch, mutation):
    audit_env.context.settings = replace(audit_env.settings, pdf_root=audit_env.root / 'export-only')
    _, db, original = _publish_attached(audit_env, monkeypatch)
    before = _review_cache._cache_path().read_bytes()
    entry = _review_cache._read_all(readonly=True)['ABCDEFGH']

    assert _review_identity.current_review_identity('ABCDEFGH', entry['review_identity']) == entry['review_identity']
    response = get(audit_env, '/api/library/deep-review/ABCDEFGH/audit')
    assert response.status_code == 403 and response.json()['error'] == 'path_not_allowed'
    assert _review_cache.get_current_review('ABCDEFGH') is not None
    assert 'ABCDEFGH' in _review_cache.current_review_keys()

    with sqlite3.connect(db) as conn:
        if mutation == 'missing_attachment':
            conn.execute('DELETE FROM itemAttachments')
        else:
            replacement = audit_env.root / 'replacement.pdf'
            replacement.write_bytes(b'A different linked attachment')
            conn.execute('UPDATE itemAttachments SET path=?', (str(replacement),))
    assert original.read_bytes() == b'Synthetic attached source'
    assert _review_cache.get_current_review('ABCDEFGH') is None
    assert 'ABCDEFGH' not in _review_cache.current_review_keys()
    assert _review_cache._cache_path().read_bytes() == before


@pytest.mark.parametrize('digest', [
    {'soundness': 'not-an-int'},
    {'key_findings': 'not-a-list'},
    {'parameters': {'metrics': 'not-a-list'}},
])
def test_legacy_export_rejects_malformed_digest_without_repair(audit_env, digest):
    _review_cache._write_one('ABCDEFGH', {'digest': digest})
    before = _review_cache._cache_path().read_bytes()

    response = get(audit_env, '/api/library/deep-review/ABCDEFGH/audit')

    assert response.status_code == 422 and response.json()['error'] == 'audit_invalid'
    assert _review_cache._cache_path().read_bytes() == before


@pytest.mark.parametrize('entry,reason', [
    ({'digest': {'tldr': 'Saved before audit receipts'}}, 'legacy_audit_unavailable'),
    ({'digest': None, 'needs_pdf': True}, 'source_unavailable'),
])
def test_valid_legacy_and_missing_pdf_export_preserve_exact_digest(audit_env, entry, reason):
    _review_cache._write_one('ABCDEFGH', entry)
    before = _review_cache._cache_path().read_bytes()

    response = get(audit_env, '/api/library/deep-review/ABCDEFGH/audit')

    assert response.status_code == 200
    assert response.json()['audit_status'] == 'unavailable' and response.json()['reason'] == reason
    assert response.json()['digest'] == entry['digest']
    assert response.json()['verification_audit'] is None and response.json()['retained_evidence'] is None
    assert _review_cache._cache_path().read_bytes() == before


@pytest.mark.parametrize('kind', ['library', 'override'])
def test_current_cache_reuses_one_source_identity_hash(audit_env, monkeypatch, kind):
    if kind == 'library':
        _, _, path = _publish_attached(audit_env, monkeypatch)
        key = 'ABCDEFGH'
    else:
        entry, _, path = publish(audit_env)
        key = 'feed:d:' + 'a' * 64
    calls = []
    original = _review_identity.file_identity

    def counted(source):
        calls.append(source)
        return original(source)

    monkeypatch.setattr(_review_identity, 'file_identity', counted)

    assert _review_cache.get_current_review(key) is not None
    assert calls == [path]
