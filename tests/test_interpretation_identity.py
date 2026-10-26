"""Exact current identities and early ordinary-cache condition admission."""
import builtins
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests._interpretation_experiment import CARD, PAYLOAD, TEXT, ScriptedClient, run_review
from tests.test_review_cache import _isolate_cache
from tests.test_verification_audit_export import audit_env, publish
from zotero_summarizer.models import PaperDigest
from zotero_summarizer.models.interpretation import InterpretationDigest
from zotero_summarizer.services.library import _digest_audit, _review_cache, _review_identity, deep_review, interpretation
from zotero_summarizer.services.library._digest_audit_export import retained_audit_status


@pytest.mark.parametrize('marker,optional', [
    (marker, optional)
    for marker in ['absent', 'unknown', None, 'paper-digest-v1', 'interpretation-digest-response-v1']
    for optional in ['payload', 'qualification', 'scientific_call', 'nested_evidence', 'nested_draft', 'nested_binding', 'marker_only']
    if marker != 'absent' or optional != 'marker_only'
])
def test_stored_condition_refuses_before_optional_import_schema_contract_or_policy(monkeypatch, marker, optional):
    entry = {'digest': PAYLOAD, 'review_contract_version': 3, 'review_identity': {}}
    if marker != 'absent':
        entry['review_identity']['interpretation_schema'] = marker
    if optional == 'payload':
        entry['interpretation'] = None
    elif optional == 'qualification':
        entry['interpretation_qualification'] = {}
    elif optional == 'scientific_call':
        entry['scientific_call'] = {}
    elif optional.startswith('nested_'):
        field = {'nested_evidence': 'interpretation_evidence', 'nested_draft': 'verified_interpretation',
                 'nested_binding': 'interpretation_bindings'}[optional]
        entry['verification_audit'] = {field: None}
    _review_cache._write_one('LIBR0038', entry)
    before = _review_cache._cache_path().read_bytes()
    forbidden = Mock(side_effect=AssertionError('condition admission must precede source/schema/projection'))
    original_import = builtins.__import__

    def guarded(name, *args, **kwargs):
        assert 'interpretation' not in name
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', guarded)
    monkeypatch.setattr(PaperDigest, 'model_validate', forbidden)
    monkeypatch.setattr(_review_identity, 'current_review_identity', forbidden)
    monkeypatch.setattr('zotero_summarizer.services.library.review_fleet.propose.project_review', forbidden)
    assert _review_cache.get_cached_review('LIBR0038') is None
    assert _review_cache.get_current_review('LIBR0038') is None
    assert _review_cache.cached_review_keys() == {'LIBR0038'}
    assert _review_cache.current_reviews() == {} and _review_cache.current_review_keys() == set()
    assert not _review_cache.review_is_current(entry, 'LIBR0038')
    assert _review_cache.export_review_audit('LIBR0038')['reason'] == 'review_condition_mismatch'
    assert _review_cache._cache_path().read_bytes() == before
    forbidden.assert_not_called()


@pytest.mark.parametrize('selection', ['unknown', '', 'paper-digest-v1', False, 0])
def test_unknown_code_selection_refuses_before_cache_schema_or_contract_io(monkeypatch, selection):
    client = ScriptedClient()
    forbidden = Mock(side_effect=AssertionError('invalid experiment condition must not do IO'))
    monkeypatch.setattr(_review_cache, '_read_all', forbidden)
    monkeypatch.setattr(PaperDigest, 'model_json_schema', forbidden)
    with pytest.raises(ValueError, match='Unknown interpretation condition'):
        run_review(client, selection)
    assert client.calls == []
    forbidden.assert_not_called()


@pytest.mark.parametrize('projected_value', [False, 0])
def test_current_basis_binding_distinguishes_false_from_zero_through_one_raw_read(projected_value):
    result = run_review(ScriptedClient())
    snapshot = deepcopy(result)
    result['digest']['parameters']['external_validation'] = projected_value
    assert _digest_audit.canonical_hash(False) != _digest_audit.canonical_hash(0)
    if projected_value is False:
        assert interpretation.qualify(result)['status'] == 'accepted'
    else:
        with pytest.raises(ValueError):
            interpretation.qualify(result, strict=False)
    assert snapshot['digest']['parameters']['external_validation'] is False


def test_verification_to_cache_binding_also_distinguishes_false_from_zero():
    result = run_review(ScriptedClient())
    assert 'parameters.external_validation: False' in result['interpretation']['claims']
    result['interpretation']['claims'] = [c.replace('False', '0') for c in result['interpretation']['claims']]
    assert interpretation.qualify(result, strict=False) == {'status': 'proof_unavailable'}
    assert interpretation.selected_original(result) == TEXT


def test_c_only_contract_drift_withholds_card_but_preserves_current_intact_base():
    # Exact invocation binding replaces the removed optional-cache fingerprint, not its drift detection.
    result = run_review(ScriptedClient())
    result['scientific_call']['scientific_binding'] = 'c' * 64
    assert interpretation.qualify(result, strict=False) == {'status': 'proof_unavailable'}
    assert interpretation.selected_original(result) == TEXT
    assert result['digest']['key_findings'] == PAYLOAD['key_findings']


@pytest.mark.parametrize('component', ['schema', 'producer', 'model'])
def test_optional_contract_drift_withholds_only_card_before_new_source_or_schema(component, monkeypatch):
    from pathlib import Path
    from zotero_summarizer.models.interpretation import InterpretationItem

    result = run_review(ScriptedClient())
    if component == 'schema':
        schema = InterpretationItem.model_json_schema()
        monkeypatch.setattr(InterpretationItem, 'model_json_schema', lambda: schema | {'description': 'Changed schema'})
    else:
        target = (Path(interpretation.__file__) if component == 'producer' else
                  Path(interpretation.__file__).parents[2] / 'models/interpretation.py')
        read = Path.read_bytes
        monkeypatch.setattr(Path, 'read_bytes', lambda path: read(path) + b'\n' if path == target else read(path))
    with pytest.raises(ValueError):
        interpretation.qualify(result)
    assert interpretation.qualify(result, strict=False) == {'status': 'proof_unavailable'}
    assert interpretation.selected_original(result) == TEXT
    assert result['digest']['key_findings'] == PAYLOAD['key_findings']


@pytest.mark.parametrize('condition', ['A', 'B'])
def test_disabled_optional_card_does_not_read_optional_contract(monkeypatch, condition):
    forbidden = Mock(side_effect=AssertionError('disabled card must not inspect optional contract'))
    monkeypatch.setattr(interpretation, '_optional_contract_sha256', forbidden)
    result = run_review(ScriptedClient(), condition)
    assert interpretation.qualify(result)['status'] == 'disabled'
    forbidden.assert_not_called()


@pytest.mark.parametrize('mutation', ['missing', 'changed'])
def test_missing_or_changed_optional_contract_never_upgrades_retained_card(mutation):
    result = run_review(ScriptedClient())
    if mutation == 'missing':
        del result['interpretation']['contract_sha256']
    else:
        result['interpretation']['contract_sha256'] = 'a' * 64
    assert interpretation.qualify(result, strict=False) == {'status': 'proof_unavailable'}
    assert interpretation.selected_original(result) == TEXT


@pytest.mark.parametrize('component', ['extraction', 'verifier'])
def test_base_contract_drift_never_becomes_optional_only_failure(monkeypatch, component):
    result = run_review(ScriptedClient())
    contracts = _digest_audit.contract_hashes()
    contracts[component + '_contract_sha256'] = 'c' * 64
    monkeypatch.setattr(interpretation, 'contract_hashes', lambda: contracts)
    with pytest.raises(ValueError, match='contract changed'):
        interpretation.qualify(result, strict=False)


@pytest.mark.parametrize('defect', ['generation_stale', 'generation_missing', 'source_stale', 'source_null',
                                  'source_hash_missing', 'source_path_null', 'source_missing', 'identity_null', 'base_null'])
def test_stale_missing_or_malformed_source_and_base_identity_is_never_forgiven(audit_env, defect):
    entry, client, source = publish(audit_env)
    identity = entry['review_identity']
    if defect == 'source_missing':
        source.unlink()
    elif defect == 'identity_null':
        entry['review_identity'] = None
    elif defect == 'base_null':
        entry['digest'] = None
    else:
        field = 'generation_sha256' if defect.startswith('generation') else 'source_path' if defect == 'source_path_null' else 'source_sha256'
        if defect.endswith('missing'):
            del identity[field]
        else:
            identity[field] = None if defect.endswith('null') else 'c' * 64
    _review_cache._write_one('LIBR0038', entry)
    before = _review_cache._cache_path().read_bytes()
    assert not _review_cache.review_is_current(entry, 'LIBR0038')
    assert _review_cache.get_current_review('LIBR0038') is None
    assert _review_cache._cache_path().read_bytes() == before
    assert client.generated == client.calls == 1


@pytest.mark.parametrize('stage', ['verified', 'cached'])
def test_noncanonical_base_types_raise_even_with_resealed_integrity(stage):
    result = run_review(ScriptedClient())
    digest = result['verification_audit']['verified_digest'] if stage == 'verified' else result['digest']
    digest['parameters']['external_validation'] = 0
    if stage == 'verified':
        result['verification_audit']['identity']['verified_digest_sha256'] = _digest_audit.canonical_hash(digest)
    with pytest.raises(ValueError):
        interpretation.qualify(result, strict=False)


def test_genuine_ordinary_legacy_and_verified_base_records_remain_readable(audit_env):
    entry, client, _ = publish(audit_env)
    key = 'feed:d:' + 'a' * 64
    _review_cache._write_one('LEGACY01', {'quality': {'grade': 'A'}})
    assert _review_cache.get_cached_review('LEGACY01') == {'quality': {'grade': 'A'}}
    assert _review_cache.get_current_review(key) is not None
    assert _review_cache.export_review_audit(key)['audit_status'] == 'available'
    assert client.generated == client.calls == 1 and 'interpretation' not in entry


@pytest.mark.parametrize('home', ['digest', 'model_digest'])
@pytest.mark.parametrize('optional', [None, [], [CARD]])
def test_unexpected_interpretation_subtype_refuses_only_after_genuine_base_publication(audit_env, monkeypatch, home, optional):
    raw = InterpretationDigest.model_validate(PAYLOAD | {'interpretation': optional})
    source = audit_env.root / 'unexpected-subtype.pdf'
    source.write_bytes(b'Owned synthetic original source bytes')
    client = ScriptedClient(cards=False, responses=[raw])
    entry = deep_review._review_one({'item_key': 'LIBR0038', 'pdf_path': str(source)},
        reader=SimpleNamespace(get_item_detail=lambda key: {'title': 'Owned'}), config=audit_env.config,
        extractor=SimpleNamespace(extract_text=lambda path: TEXT), llm=client, quality_enabled=True)
    assert entry['model_digest']['interpretation'] == optional and entry['digest']['interpretation'] == optional
    other = 'model_digest' if home == 'digest' else 'digest'
    del entry[other]['interpretation']
    current = _review_identity.current_review_identity('LIBR0038', entry['review_identity'])
    assert current == entry['review_identity']
    assert retained_audit_status(entry, current_identity=current) == ('available', '')
    _review_cache._write_one('LIBR0038', entry)
    before = _review_cache._cache_path().read_bytes()
    forbidden = Mock(side_effect=AssertionError('reserved nested payload must refuse before projection or optional work'))
    original_import = builtins.__import__
    def guarded(name, *args, **kwargs):
        assert 'interpretation' not in name
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', guarded)
    monkeypatch.setattr(interpretation, 'qualify', forbidden)
    monkeypatch.setattr(_review_identity, 'current_review_identity', forbidden)
    monkeypatch.setattr('zotero_summarizer.services.library.review_fleet.propose.project_review', forbidden)
    assert _review_cache.get_cached_review('LIBR0038') is None
    assert _review_cache.get_current_review('LIBR0038') is None
    assert _review_cache.current_reviews() == {} and _review_cache.current_review_keys() == set()
    assert _review_cache.export_review_audit('LIBR0038')['reason'] == 'review_condition_mismatch'
    assert _review_cache._cache_path().read_bytes() == before
    forbidden.assert_not_called()


def test_unrelated_subtype_metadata_preserves_accepted_original_base_proof(audit_env):
    entry, client, _ = publish(audit_env)
    key = 'feed:d:' + 'a' * 64
    for field in ('digest', 'model_digest'):
        entry[field]['unrelated_subtype_metadata'] = {'duplicate': ['π', 'π'], 'observed': False, 'count': 0}
    _review_cache._write_one(key, entry)
    before = _review_cache._cache_path().read_bytes()
    view = _review_cache.get_current_review(key)
    assert view is not None
    assert view['digest']['unrelated_subtype_metadata'] == entry['digest']['unrelated_subtype_metadata']
    assert _review_cache.export_review_audit(key)['audit_status'] == 'available'
    assert _review_cache._cache_path().read_bytes() == before and client.generated == client.calls == 1
