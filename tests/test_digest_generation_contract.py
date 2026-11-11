"""Generation schemas express publication requirements without changing legacy reads."""
from copy import deepcopy

import pytest
from pydantic import ValidationError

from test_digest_verification import _digest
from zotero_summarizer.models import PaperDigest
from zotero_summarizer.models.digest_generation import (
    _ASSESSMENT_FIELDS, FeedDigestGeneration, InterpretationDigestGeneration, PaperDigestGeneration,
)
from zotero_summarizer.services.library import quality_review
from zotero_summarizer.services.library._review_attempt import Attempt
from zotero_summarizer.services.setup.bootstrap import _default_goals_config


@pytest.mark.parametrize('model', [PaperDigestGeneration, FeedDigestGeneration, InterpretationDigestGeneration])
@pytest.mark.parametrize('missing', ['methods', 'key_findings', 'limitations', 'executive_summary', 'soundness', 'confidence'])
def test_new_generation_cannot_hide_missing_review_content_behind_storage_defaults(model, missing):
    value = _digest().model_dump(exclude={'basis'})
    del value[missing]
    legacy = PaperDigest.model_validate(value)
    assert missing not in legacy.model_fields_set
    with pytest.raises(ValidationError):
        model.model_validate(value)
    with pytest.raises(quality_review.DigestGenerationFailed, match='omitted required review content'):
        quality_review._coerce_digest(legacy)


@pytest.mark.parametrize('model', [PaperDigestGeneration, FeedDigestGeneration, InterpretationDigestGeneration])
@pytest.mark.parametrize('missing', _ASSESSMENT_FIELDS)
def test_generation_requires_explicit_fields_while_legacy_storage_remains_readable(model, missing):
    value = _digest().model_dump(exclude_unset=True)
    del value[missing]
    assert missing not in PaperDigest.model_validate(value).model_fields_set
    with pytest.raises(ValidationError):
        model.model_validate(value)


@pytest.mark.parametrize('model', [PaperDigestGeneration, FeedDigestGeneration, InterpretationDigestGeneration])
def test_generation_schema_nonnull_friction_and_action_enum_preserve_valid_empty_skip(model):
    schema = model.model_json_schema()
    assert set(_ASSESSMENT_FIELDS) <= set(schema['required'])
    assert schema['properties']['read_decision']['enum'] == ['read', 'skim', 'skip']
    assert schema['properties']['writing_friction']['enum'] == ['low', 'moderate', 'high']
    value = _digest().model_dump(exclude_unset=True)
    assert model.model_validate(value).read_decision == 'skip'
    for field, invalid in [('writing_friction', None), ('read_decision', 'invented')]:
        with pytest.raises(ValidationError):
            model.model_validate({**value, field: invalid})
    assert PaperDigest().writing_friction is None


@pytest.mark.parametrize('phase', ['initial', 'parse_retry', 'correction'])
def test_selected_generation_dto_and_schema_stay_aligned_across_bounded_calls(monkeypatch, phase):
    calls = []
    bad = '{"read_decision":"skip"}'

    class Client:
        def pydantic_prompt(self, **kwargs):
            calls.append(kwargs)
            return bad if phase == 'parse_retry' and len(calls) == 1 else _digest()

    checks = []

    def verify(*_args, claims=None, **_kwargs):
        from zotero_summarizer.services.library._digest_verification import DigestSourceRejected, _Checks
        checks.append(True)
        if phase == 'correction' and len(checks) == 1:
            raise DigestSourceRejected('Scripted source rejection', failure_kind='verifier_support_not_established',
                                       rejected_claims=['controversies: x'])
        return None if claims is None else _Checks(checks=[{'index': i, 'supported': True, 'evidence': [0]}
                                                           for i, _ in enumerate(claims)])

    monkeypatch.setattr(quality_review, '_verify_generated_digest', verify)
    original = quality_review.build_response_format(PaperDigest)
    before = deepcopy(original)
    result = quality_review.assess_digest(title='Fixture', full_text='Original source body',
        config=_default_goals_config(), llm=Client(), response_format=original)
    assert isinstance(result, PaperDigest)
    assert len(calls) == (1 if phase == 'initial' else 2)
    assert original == before
    for call in calls:
        # the bounded correction is the one small field-repair call: its own DTO and schema
        expected = calls[-1]['pydantic_model'] if phase == 'correction' and call is calls[-1] else PaperDigestGeneration
        assert call['pydantic_model'] is expected
        assert call['response_format'] == quality_review.build_response_format(expected)


def test_metadata_explains_supplied_field_shape_without_generated_values():
    value = PaperDigest(tldr='sensitive-generated-prose')
    with Attempt() as attempt:
        attempt.record('generator', value, kind='parsed_response')
    event = attempt.metadata()['events'][0]
    assert event['supplied_digest_fields'] == ['tldr']
    assert 'sensitive' not in str(attempt.metadata())


def test_generation_failure_uses_allowlisted_reason_without_validation_inputs():
    from zotero_summarizer.services.library._deep_review_errors import diagnostic_for_exception
    error = quality_review.DigestGenerationFailed('sensitive-generated-validation-input')
    diagnostic = diagnostic_for_exception(error)
    assert diagnostic['reason_code'] == 'digest_schema_invalid'
    assert 'sensitive' not in str(diagnostic)
