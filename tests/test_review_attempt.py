from test_digest_verification import PAPER, _Verifier, _digest
from test_review_source_boundary import _Generator
from zotero_summarizer.services.library._review_attempt import Attempt
from zotero_summarizer.services.library.quality_review import assess_digest
from zotero_summarizer.services.setup.bootstrap import _default_goals_config


def test_metadata_default_records_hashes_without_prompts_or_values():
    with Attempt() as attempt:
        assess_digest(title='Paper', full_text=PAPER, config=_default_goals_config(),
                      llm=_Generator(_digest()), verifier_llm=_Verifier())
    meta = attempt.metadata()
    assert meta['stage_counts']['generator:prompt'] == 1
    assert meta['stage_counts']['verifier:parsed_response'] >= 1
    assert PAPER not in str(meta)
    original = next(event for event in meta['events'] if event['stage'] == 'original')
    assert original['characters'] == len(PAPER)
    assert all('decoded_value' not in event for event in attempt.events)


def test_correction_is_separate_and_403_occurrence_does_not_authorize_access_claim():
    import pytest
    from zotero_summarizer.services.library._digest_verification import DigestSourceRejected

    source = PAPER + ' Appendix identifier 403.'
    bad = _digest().model_copy(update={'read_why': 'Full text is inaccessible (HTTP 403 from MDPI);'})
    generator = _Generator(bad)
    with Attempt(capture=True) as attempt:
        with pytest.raises(DigestSourceRejected, match='unsupported fields'):
            assess_digest(title='Paper', full_text=source, config=_default_goals_config(),
                          llm=generator, verifier_llm=_Verifier(supported=False))
    assert attempt.metadata()['stage_counts']['correction:prompt'] == 1
    assert len(generator.prompts) == 2


def test_capture_freezes_decoded_return_values():
    value = {'checks': [{'supported': True}]}
    attempt = Attempt(capture=True)
    attempt.record('verifier', value, kind='parsed_response')
    value['checks'][0]['supported'] = False
    assert attempt.events[0]['decoded_value']['checks'][0]['supported'] is True


def test_explicit_capture_separates_original_generation_and_decoded_values(tmp_path):
    with Attempt(capture=True) as attempt:
        assess_digest(title='Paper', full_text=PAPER, config=_default_goals_config(),
                      llm=_Generator(_digest()), verifier_llm=_Verifier())
    assert any(event['stage'] == 'original' and event['decoded_value'] == PAPER for event in attempt.events)
    assert any(event['stage'] == 'verifier' and event['kind'] == 'parsed_response' for event in attempt.events)
    path = attempt.save(tmp_path / 'capture')
    assert 'not-raw-transport' in path.read_text()
    assert path.stat().st_mode & 0o777 == 0o600
