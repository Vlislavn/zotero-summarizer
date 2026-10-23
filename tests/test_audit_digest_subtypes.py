"""Optional runtime metadata stays stored, not part of verification authority."""
from copy import deepcopy
from typing import Any

import pytest

import test_verification_audit_export as fixtures
from test_verification_audit_export import audit_env, get, publish  # noqa: F401
from zotero_summarizer.models import PaperDigest
from zotero_summarizer.services.library import _digest_audit, _review_cache


class EnrichedDigest(PaperDigest):
    unrelated_metadata: Any = None


@pytest.mark.parametrize('metadata', [None, {'nested': [1, 1.0, False]}])
def test_subtype_publication_and_export_preserve_metadata_outside_audit(audit_env, monkeypatch, metadata):
    class EnrichedClient(fixtures.ScriptedClient):
        def pydantic_prompt(self, **kwargs):
            result = super().pydantic_prompt(**kwargs)
            if isinstance(result, PaperDigest):
                return EnrichedDigest.model_validate({**result.model_dump(), 'unrelated_metadata': metadata})
            return result

    monkeypatch.setattr(fixtures, 'ScriptedClient', EnrichedClient)
    entry, _, _ = publish(audit_env)
    original = deepcopy(entry)

    response = get(audit_env)

    assert response.status_code == 200
    payload = response.json()
    assert payload['audit_status'] == 'available'
    assert payload['digest'] == original['digest'] and payload['digest']['unrelated_metadata'] == metadata
    audit = payload['verification_audit']
    assert 'unrelated_metadata' not in audit['verified_digest']
    assert 'unrelated_metadata' not in audit['post_verification_changes']
    declared = {k: v for k, v in entry['digest'].items() if k in PaperDigest.model_fields}
    assert audit['identity']['cached_digest_sha256'] == _digest_audit.canonical_hash(declared)
    assert _review_cache.get_current_review(fixtures.KEY) is not None
    assert _review_cache._read_all(readonly=True)[fixtures.KEY] == original

    entry['digest']['unrelated_metadata'] = {'changed': 'still not verifier authority'}
    _review_cache._write_one(fixtures.KEY, entry)
    before = _review_cache._cache_path().read_bytes()
    updated = get(audit_env).json()
    assert updated['audit_status'] == 'available'
    assert updated['digest'] == entry['digest']
    assert updated['verification_audit'] == audit
    assert _review_cache.get_current_review(fixtures.KEY) is not None
    assert _review_cache._cache_path().read_bytes() == before
