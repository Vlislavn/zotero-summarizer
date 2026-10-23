"""The verifier contract excludes unrelated enrichment without mutating originals."""
from typing import Any

import pytest

from tests.test_digest_verification import _digest
from zotero_summarizer.models import PaperDigest
from zotero_summarizer.services.library import _digest_audit, quality_review


class EnrichedDigest(PaperDigest):
    unrelated_metadata: Any = None


@pytest.mark.parametrize("metadata", [None, {"source": "synthetic"}, {"nested": [1, 1.0, False]}])
def test_unrelated_subtype_fields_are_not_attempt_or_verified_digest_authority(metadata):
    base = _digest()
    enriched = EnrichedDigest.model_validate({**base.model_dump(), "unrelated_metadata": metadata})
    original = enriched.model_dump(mode="json")
    expected = base.model_dump(mode="json")

    attempt = quality_review._verification_attempt(enriched, False)

    assert attempt["digest_sha256"] == _digest_audit.canonical_hash(expected)
    assert _digest_audit.digest_contract_payload(enriched) == expected
    assert enriched.model_dump(mode="json") == original
    assert original != expected


def test_plain_digest_payload_is_unchanged():
    digest = _digest()
    attempt = quality_review._verification_attempt(digest, False)
    assert attempt["digest_sha256"] == _digest_audit.canonical_hash(digest.model_dump(mode="json"))
