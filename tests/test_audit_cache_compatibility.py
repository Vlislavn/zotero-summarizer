"""Native cache ownership must not inherit the export-only PDF root restriction."""
from copy import deepcopy
from dataclasses import replace
import sqlite3

import pytest

from test_verification_audit_export import (  # noqa: F401
    KEY, ScriptedClient, audit_env, _publish_attached, publish,
)
from zotero_summarizer.models import PaperDigest, QualityEval
from zotero_summarizer.api.errors import APIError
from zotero_summarizer.services.library import _review_cache, _review_identity


@pytest.mark.parametrize('mutation', ['changed_attachment', 'missing_attachment'])
def test_linked_attachment_outside_export_roots_keeps_only_unchanged_cache(audit_env, monkeypatch, mutation):
    audit_env.context.settings = replace(audit_env.settings, pdf_root=audit_env.root / 'export-only')
    _, db, original = _publish_attached(audit_env, monkeypatch)
    before = _review_cache._cache_path().read_bytes()
    entry = _review_cache._read_all(readonly=True)['ABCDEFGH']

    assert _review_identity.current_review_identity('ABCDEFGH', entry['review_identity']) == entry['review_identity']
    with pytest.raises(APIError) as error:
        _review_cache.export_review_audit('ABCDEFGH')
    assert (error.value.status_code, error.value.error) == (403, 'path_not_allowed')
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

    with pytest.raises(APIError) as error:
        _review_cache.export_review_audit('ABCDEFGH')
    assert (error.value.status_code, error.value.error) == (422, 'audit_invalid')
    assert _review_cache._cache_path().read_bytes() == before


@pytest.mark.parametrize('entry,reason', [
    ({'digest': {'tldr': 'Saved before audit receipts'}}, 'legacy_audit_unavailable'),
    ({'digest': None, 'needs_pdf': True}, 'source_unavailable'),
])
def test_valid_legacy_and_missing_pdf_export_preserve_exact_digest(audit_env, entry, reason):
    _review_cache._write_one('ABCDEFGH', entry)
    before = _review_cache._cache_path().read_bytes()

    payload = _review_cache.export_review_audit('ABCDEFGH')
    assert payload['audit_status'] == 'unavailable' and payload['reason'] == reason
    assert payload['digest'] == entry['digest']
    assert payload['verification_audit'] is None and payload['retained_evidence'] is None
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


def _publish_with_grade(env, monkeypatch, grade):
    original = ScriptedClient.pydantic_prompt
    def generated(self, *, pydantic_model, **kwargs):
        result = original(self, pydantic_model=pydantic_model, **kwargs)
        return result.model_copy(update={'grade': grade}) if pydantic_model is PaperDigest else result
    monkeypatch.setattr(ScriptedClient, 'pydantic_prompt', generated)
    return publish(env)


@pytest.mark.parametrize('grade', ['', 'A', 'D'])
def test_current_readers_admit_retained_artifact_before_policy_projection(audit_env, monkeypatch, grade):
    from zotero_summarizer.services.library._digest_audit_export import retained_audit_status
    from zotero_summarizer.services.library.review_fleet import fleet

    raw, client, path = _publish_with_grade(audit_env, monkeypatch, grade)
    before = _review_cache._cache_path().read_bytes(), path.read_bytes()
    assert raw['model_digest']['grade'] == grade
    assert retained_audit_status(raw) == ('available', '')

    single = _review_cache.get_current_review(KEY)
    bulk = _review_cache.current_reviews()

    assert single is not None and single == bulk[KEY] == fleet._current_cache(KEY)
    assert KEY in _review_cache.current_review_keys()
    assert single['digest']['grade'] == ''
    assert single['model_digest']['grade'] == grade
    assert single['verification_audit'] == raw['verification_audit']
    if grade:
        assert retained_audit_status(single) == ('stale', 'cached_artifact_changed')
    assert _review_cache.export_review_audit(KEY)['digest'] == raw['digest']
    assert _review_cache.export_review_audit(KEY)['audit_status'] == 'available'
    assert (client.generated, client.calls) == (1, 1)
    assert (_review_cache._cache_path().read_bytes(), path.read_bytes()) == before


@pytest.mark.parametrize('mutation', [
    'model_tldr', 'model_findings', 'model_novelty', 'quality', 'goals', 'raw_decision', 'missing_model',
])
def test_current_readers_refuse_substituted_reading_policy_inputs(audit_env, mutation):
    from zotero_summarizer.services.library._digest_audit_export import retained_audit_status
    from zotero_summarizer.services.library.review_fleet import fleet

    raw, client, path = publish(audit_env)
    changed = deepcopy(raw)
    if mutation == 'model_tldr':
        changed['model_digest']['tldr'] = 'Unverified replacement <payload> Ω.'
    elif mutation == 'model_findings':
        changed['model_digest']['key_findings'].append('Unverified replacement finding.')
    elif mutation == 'model_novelty':
        changed['model_digest']['novelty'] = 5
    elif mutation == 'quality':
        changed['quality'] = {'basis': 'full_text', 'grade': 'A', 'quality_band': 'highlight'}
    elif mutation == 'goals':
        changed['goal_summaries'] = [{'retrieval_state': 'hit', 'relevant': True, 'abstained': False}]
    elif mutation == 'missing_model':
        del changed['model_digest']
    else:
        changed['model_read_decision'] = 'read'
    _review_cache._write_one(KEY, changed)
    before = _review_cache._cache_path().read_bytes(), path.read_bytes()

    assert retained_audit_status(changed) == ('stale', 'reading_policy_inputs_changed')
    assert _review_cache.get_current_review(KEY) is None
    assert KEY not in _review_cache.current_review_keys()
    assert KEY not in _review_cache.current_reviews()
    assert fleet._current_cache(KEY) is None
    assert (client.generated, client.calls) == (1, 1)
    assert (_review_cache._cache_path().read_bytes(), path.read_bytes()) == before


def test_equivalent_unassessed_quality_keeps_normalized_input_binding(audit_env):
    from zotero_summarizer.services.library._digest_audit_export import retained_audit_status

    raw, _, _ = publish(audit_env)
    changed = deepcopy(raw)
    assert changed['quality'] is None
    changed['quality'] = QualityEval(quality_band='uncertain', basis='not_assessed').model_dump()
    _review_cache._write_one(KEY, changed)
    before = _review_cache._cache_path().read_bytes()

    assert retained_audit_status(changed) == ('available', '')
    assert _review_cache.get_current_review(KEY) == _review_cache.current_reviews()[KEY]
    assert _review_cache._cache_path().read_bytes() == before


@pytest.mark.parametrize('kind', ['missing_current', 'mismatched_current', 'missing_old_contract'])
def test_policy_binding_field_preserves_malformed_vs_stale_contract_admission(audit_env, kind):
    from zotero_summarizer.services.library._digest_audit import payload_hash

    raw, _, _ = publish(audit_env)
    changed = deepcopy(raw)
    audit = changed['verification_audit']
    if kind == 'mismatched_current':
        audit['identity']['reading_policy_inputs_sha256'] = '0' * 64
    else:
        del audit['identity']['reading_policy_inputs_sha256']
    if kind == 'missing_old_contract':
        audit['identity']['verifier_contract_sha256'] = '0' * 64
    audit['identity']['audit_sha256'] = payload_hash(audit)
    _review_cache._write_one(KEY, changed)
    before = _review_cache._cache_path().read_bytes()

    assert _review_cache.get_current_review(KEY) is None
    if kind == 'missing_current':
        with pytest.raises(APIError) as error:
            _review_cache.export_review_audit(KEY)
        assert (error.value.status_code, error.value.error) == (422, 'audit_invalid')
    else:
        result = _review_cache.export_review_audit(KEY)
        assert result['audit_status'] == 'stale'
        assert result['reason'] == ('audit_contract_changed' if kind == 'missing_old_contract'
                                    else 'reading_policy_inputs_changed')
    assert _review_cache._cache_path().read_bytes() == before


@pytest.mark.parametrize('audit_kind', ['missing', 'null'])
@pytest.mark.parametrize('keep_model_snapshot', [True, False])
def test_missing_audit_cannot_upgrade_current_generated_digest(audit_env, audit_kind, keep_model_snapshot):
    from zotero_summarizer.services.library.review_fleet import fleet

    raw, client, path = publish(audit_env)
    changed = deepcopy(raw)
    changed['model_digest']['tldr'] = 'Unverified replacement <payload> Ω.'
    if not keep_model_snapshot:
        del changed['model_digest']
    if audit_kind == 'missing':
        del changed['verification_audit']
    else:
        changed['verification_audit'] = None
    _review_cache._write_one(KEY, changed)
    before = _review_cache._cache_path().read_bytes(), path.read_bytes()

    assert _review_cache.get_cached_review(KEY) is not None
    assert _review_cache.export_review_audit(KEY)['audit_status'] == 'unavailable'
    assert _review_cache.get_current_review(KEY) is None
    assert KEY not in _review_cache.current_reviews()
    assert KEY not in _review_cache.current_review_keys()
    assert fleet._current_cache(KEY) is None
    assert (client.generated, client.calls) == (1, 1)
    assert (_review_cache._cache_path().read_bytes(), path.read_bytes()) == before


@pytest.mark.parametrize('grade', ['', 'A', 'D'])
def test_copy_keeps_exact_retained_artifact_receipt_and_source_revision(audit_env, monkeypatch, grade):
    raw, client, path = _publish_with_grade(audit_env, monkeypatch, grade)
    _, revision = _review_cache.publication_snapshot(KEY)
    source = path.read_bytes()

    assert _review_cache.copy_review(KEY, 'COPYAAAA') is True

    copied, copy_revision = _review_cache.publication_snapshot('COPYAAAA')
    assert copied == raw
    assert _review_cache.publication_snapshot(KEY) == (raw, revision)
    assert copy_revision and copy_revision != revision
    assert _review_cache.export_review_audit('COPYAAAA')['audit_status'] == 'available'
    assert _review_cache.get_current_review('COPYAAAA') == _review_cache.get_current_review(KEY)
    assert copied['verification_audit'] == raw['verification_audit']
    assert (client.generated, client.calls) == (1, 1)
    assert path.read_bytes() == source


def test_missing_source_placeholder_is_current_without_becoming_verified(audit_env):
    raw, _, path = publish(audit_env)
    path.unlink()
    identity = _review_identity.build_review_identity(
        config=audit_env.config, pdf_path=str(path), source_kind='override', focus_prompt='')
    missing = {'review_contract_version': _review_cache.REVIEW_CONTRACT_VERSION,
               'review_identity': identity, 'digest': None, 'needs_pdf': True}
    _review_cache._write_one(KEY, missing)
    before = _review_cache._cache_path().read_bytes()

    assert identity['source_sha256'] == 'missing'
    assert _review_cache.get_current_review(KEY) == missing
    assert _review_cache.current_reviews()[KEY] == missing
    assert _review_cache.export_review_audit(KEY)['audit_status'] == 'unavailable'
    assert _review_cache._cache_path().read_bytes() == before


@pytest.mark.parametrize('snapshot', ['absent', 'null', 'empty', 'retained'])
def test_placeholder_cannot_reconstruct_a_current_unaudited_digest(audit_env, snapshot):
    from zotero_summarizer.services.library.review_fleet import fleet

    raw, client, path = publish(audit_env)
    path.unlink()
    identity = _review_identity.build_review_identity(
        config=audit_env.config, pdf_path=str(path), source_kind='override', focus_prompt='')
    entry = {'review_contract_version': _review_cache.REVIEW_CONTRACT_VERSION,
             'review_identity': identity, 'digest': None, 'needs_pdf': True}
    if snapshot == 'null':
        entry['model_digest'] = None
    elif snapshot == 'empty':
        entry['model_digest'] = {}
    elif snapshot == 'retained':
        entry['model_digest'] = {**raw['model_digest'], 'tldr': 'Unaudited reconstructed claim Ω.'}
    _review_cache._write_one(KEY, entry)
    before = _review_cache._cache_path().read_bytes()

    view = _review_cache.get_current_review(KEY)

    if snapshot == 'retained':
        assert view is None
        assert KEY not in _review_cache.current_reviews()
        assert KEY not in _review_cache.current_review_keys()
    else:
        assert view == entry
        assert view['digest'] is None and view['needs_pdf'] is True
        assert _review_cache.current_reviews()[KEY] == entry
        assert KEY in _review_cache.current_review_keys()
    assert fleet._has_digest(KEY) is False
    assert (client.generated, client.calls) == (1, 1)
    assert _review_cache._cache_path().read_bytes() == before


@pytest.mark.parametrize('key', ['', None])
def test_empty_current_review_key_does_not_read_cache(monkeypatch, key):
    def forbidden(*args, **kwargs):
        pytest.fail('An absent key must not read or recover cache state')
    monkeypatch.setattr(_review_cache, '_read_all', forbidden)
    assert _review_cache.get_current_review(key) is None
