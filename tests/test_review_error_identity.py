import pytest

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.services.library import deep_review


@pytest.fixture(autouse=True)
def isolated_jobs():
    with deep_review._LOCK:
        previous = dict(deep_review._JOBS)
        deep_review._JOBS.clear()
    yield
    with deep_review._LOCK:
        deep_review._JOBS.clear()
        deep_review._JOBS.update(previous)


def test_worker_preserves_operational_identity_in_keyed_and_aggregate_status(monkeypatch):
    def fail(*args, **kwargs):
        raise APIError('source_unusable', 'Cannot review source', 422, {
            'stage': 'source_admission', 'recovery': 'Choose an original paper source',
            'headers': {'Authorization': 'secret'},
        })

    monkeypatch.setattr(deep_review, '_review_one', fail)
    deep_review._review_worker({'item_key': 'A'}, {}, '')
    import asyncio
    from zotero_summarizer.api.routes.library import get_deep_review_status

    for result in (deep_review.status('A'), deep_review.status(), asyncio.run(get_deep_review_status('A'))):
        assert result['diagnostic'] == {
            'code': 'source_unusable', 'stage': 'source_admission',
            'recovery': 'Choose an original paper source',
        }
        assert result['error']
        assert 'secret' not in str(result)


def test_value_error_prose_does_not_invent_http_or_auth_identity(monkeypatch):
    def fail(*args, **kwargs):
        raise ValueError('Model said HTTP 403 access denied')

    monkeypatch.setattr(deep_review, '_review_one', fail)
    deep_review._review_worker({'item_key': 'A'}, {}, '')
    assert deep_review.status('A')['diagnostic'] == {
        'code': 'review_validation_failed', 'stage': 'review',
        'recovery': 'Inspect source verification and retry the review',
    }


def test_production_verifier_rejection_preserves_origin_in_mixed_jobs(monkeypatch):
    from test_digest_verification import PAPER, _Verifier, _digest
    from test_review_source_boundary import _Generator
    from zotero_summarizer.services.library.quality_review import assess_digest
    from zotero_summarizer.services.setup.bootstrap import _default_goals_config

    generators = {}
    def review(item, **kwargs):
        key = item['item_key']
        generators[key] = _Generator(_digest())
        assess_digest(title='Paper', full_text=PAPER, config=_default_goals_config(),
                      llm=generators[key], verifier_llm=_Verifier(supported=key == 'GOOD'))
        return None

    monkeypatch.setattr(deep_review, '_review_one', review)
    deep_review._review_worker({'item_key': 'BAD'}, {}, '')
    deep_review._review_worker({'item_key': 'GOOD'}, {}, '')
    assert deep_review.status('BAD')['diagnostic']['code'] == 'digest_source_rejected'
    assert deep_review.status('BAD')['diagnostic']['stage'] == 'verification'
    assert deep_review.status('GOOD')['status'] == 'ready'
    assert deep_review.status('GOOD')['diagnostic'] is None
    assert len(generators['BAD'].prompts) == 2
    assert len(generators['GOOD'].prompts) == 1


def test_unavailable_verifier_is_typed_and_does_not_regenerate(monkeypatch):
    from test_digest_verification import PAPER, _digest
    from test_review_source_boundary import _Generator
    from zotero_summarizer.services.library.quality_review import assess_digest
    from zotero_summarizer.services.setup.bootstrap import _default_goals_config

    class Malformed(_Generator):
        def pydantic_prompt(self, **kwargs):
            self.prompts.append(kwargs['prompt'])
            if kwargs['pydantic_model'].__name__ == 'PaperDigest':
                return _digest()
            return ''

    generator = Malformed(_digest())
    def review(*args, **kwargs):
        assess_digest(title='Paper', full_text=PAPER, config=_default_goals_config(), llm=generator)

    monkeypatch.setattr(deep_review, '_review_one', review)
    deep_review._review_worker({'item_key': 'A'}, {}, '')
    assert deep_review.status('A')['diagnostic']['code'] == 'digest_verifier_unavailable'
    assert deep_review.status('A')['diagnostic']['stage'] == 'verification'
    assert len(generator.prompts) == 3  # One generation; two verifier contract attempts.


def test_completed_attempt_cannot_overwrite_metadata_of_new_same_item_job(monkeypatch):
    original_set_job = deep_review._set_job
    def settle_and_restart(item_key, **fields):
        original_set_job(item_key, **fields)
        if fields.get('status') == 'ready':
            with deep_review._LOCK:
                deep_review._JOBS[item_key] = {'status': 'running', 'progress': {}, 'attempt': None}

    monkeypatch.setattr(deep_review, '_set_job', settle_and_restart)
    monkeypatch.setattr(deep_review, '_review_one', lambda *args, **kwargs: None)
    deep_review._review_worker({'item_key': 'A'}, {}, '')
    assert deep_review.status('A')['status'] == 'running'
    assert deep_review.status('A')['attempt'] is None


def test_success_clears_prior_diagnostic(monkeypatch):
    deep_review._set_job('A', diagnostic={'code': 'old'})
    monkeypatch.setattr(deep_review, '_review_one', lambda *args, **kwargs: None)
    deep_review._review_worker({'item_key': 'A'}, {}, '')
    assert deep_review.status('A')['diagnostic'] is None


def test_source_unavailable_placeholder_preserves_current_usable_review(monkeypatch):
    prior = {'digest': {'tldr': 'Prior verified paper review'}}
    writes = []
    monkeypatch.setattr(deep_review, 'get_current_review', lambda key: prior)
    monkeypatch.setattr(deep_review, '_review_one', lambda *a, **kw: {'needs_pdf': True, 'digest': None})
    monkeypatch.setattr(deep_review, '_write_one', lambda *args: writes.append(args))
    monkeypatch.setattr(deep_review, '_try_rebuild_render', lambda *args: None)
    deep_review._review_worker({'item_key': 'A'}, {}, '')
    assert writes == []
    result = deep_review.status('A')
    assert result['status'] == 'ready'
    terminal = next(e for e in result['attempt']['events'] if e['stage'] == 'terminal')
    assert terminal['identity']['outcome'] == 'source_unavailable'
    assert terminal['identity']['counts_as_verified_review'] is False
