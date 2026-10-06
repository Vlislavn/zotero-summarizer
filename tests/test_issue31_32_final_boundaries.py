from types import SimpleNamespace

import pytest


def test_complementary_boolean_query_does_not_make_first_concept_mandatory():
    from zotero_summarizer.services.search._models import SearchIntent
    from zotero_summarizer.services.search.intent import build_query_plan
    intent = SearchIntent(raw_query='Compare verification and validation', concepts=['verification', 'validation'],
                          constraint_origin='model_proposed')
    plan = build_query_plan(intent)
    assert any('"verification" OR "validation"' in q and not q.startswith('"verification" AND')
               for q in plan.europepmc_variants)
    assert any(' OR ' in q for q in plan.openalex_lexical_variants)


def test_every_digest_template_gets_supplied_source_contract():
    from test_digest_verification import PAPER, _Verifier, _digest
    from test_review_source_boundary import _Generator
    from zotero_summarizer.services.library.quality_review import assess_digest
    from zotero_summarizer.services.setup.bootstrap import _default_goals_config
    config = _default_goals_config()
    config.prompts.paper_digest = 'Title: {title}; Goals: {research_goals}; Text: {full_text}'
    generator = _Generator(_digest())
    assess_digest(title='Paper', full_text=PAPER, config=config, llm=generator, verifier_llm=_Verifier())
    assert 'already supplied' in generator.prompts[0]
    assert 'acquisition' in generator.prompts[0].casefold()


def test_final_malformed_generation_retains_valueerror_identity_and_stage():
    from test_digest_verification import PAPER
    from zotero_summarizer.services.library.quality_review import assess_digest
    from zotero_summarizer.services.library._deep_review_errors import diagnostic_for_exception
    from zotero_summarizer.services.setup.bootstrap import _default_goals_config
    client = SimpleNamespace(pydantic_prompt=lambda **kwargs: 'not a digest')
    with pytest.raises(ValueError) as failure:
        assess_digest(title='Paper', full_text=PAPER, config=_default_goals_config(), llm=client)
    assert diagnostic_for_exception(failure.value)['stage'] == 'generation'


def test_cli_denied_source_rejected_before_any_client_build(tmp_path, monkeypatch):
    import json
    from test_source_admission import DENIAL
    from zotero_summarizer.cli import _app, build_parser
    from zotero_summarizer.api.errors import APIError
    from zotero_summarizer.services.llm import factory
    from zotero_summarizer.services.setup.bootstrap import _default_goals_config
    from zotero_summarizer.services import _common
    path = tmp_path / 'paper_render' / 'A' / 'paper_read.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'full_text': DENIAL, 'title': 'Legacy denied artifact'}))
    monkeypatch.setattr(_app.Settings, 'load', lambda **kwargs: SimpleNamespace(data_dir=tmp_path, config_path=tmp_path / 'config'))
    monkeypatch.setattr(_common, 'read_config', lambda path: _default_goals_config())
    def forbidden(*args, **kwargs):
        pytest.fail('Client build happened before source admission')
    monkeypatch.setattr(factory, 'build_client_for_stage', forbidden)
    monkeypatch.setattr(factory, 'build_client_for_provider', forbidden)
    args = build_parser().parse_args(['verify-deep-review', '--item-key', 'A', '--capture-local'])
    with pytest.raises(APIError, match='operational response'):
        _app._verify_deep_review(args)
    captured = json.loads(next((tmp_path / 'deep_review_attempts').glob('*/attempt.json')).read_text())
    assert captured['metadata']['stage_counts']['terminal:failure_identity'] == 1
    assert any(e.get('identity', {}).get('scope') == 'legacy_full_text_unverified' for e in captured['metadata']['events'])


@pytest.mark.parametrize('correction', [False, True])
def test_client_raised_validation_keeps_generation_origin(correction):
    from test_digest_verification import PAPER, _Verifier, _digest
    from zotero_summarizer.services.library.quality_review import assess_digest
    from zotero_summarizer.services.library._deep_review_errors import diagnostic_for_exception
    from zotero_summarizer.services.setup.bootstrap import _default_goals_config
    calls = []
    def generate(**kwargs):
        calls.append(kwargs)
        if correction and len(calls) == 1:
            return _digest()
        raise ValueError('client output invalid')
    with pytest.raises(ValueError) as failure:
        assess_digest(title='Paper', full_text=PAPER, config=_default_goals_config(),
                      llm=SimpleNamespace(pydantic_prompt=generate), verifier_llm=_Verifier(supported=False))
    assert len(calls) == 2
    assert diagnostic_for_exception(failure.value)['stage'] == 'generation'


@pytest.mark.parametrize('sdk_name', ['openai', 'anthropic'])
@pytest.mark.parametrize('stage', ['generator', 'verifier'])
def test_sdk_connection_failure_is_typed_at_actual_call_origin(sdk_name, stage):
    import importlib
    import httpx
    from zotero_summarizer.api.errors import APIError
    from zotero_summarizer.services.library._review_attempt import observed_client
    sdk = importlib.import_module(sdk_name)
    def unavailable(**kwargs):
        raise sdk.APIConnectionError(request=httpx.Request('POST', 'https://private.invalid/token'))
    with pytest.raises(APIError) as failure:
        observed_client(SimpleNamespace(pydantic_prompt=unavailable), stage).pydantic_prompt(prompt='Source')
    assert failure.value.details['stage'] == ('generation' if stage == 'generator' else 'verification')
    assert 'private.invalid' not in str(failure.value)
    assert 'http_status' not in failure.value.details


def test_invented_domain_is_visible_not_a_mandatory_anchor():
    from zotero_summarizer.services.search._models import SearchIntent
    from zotero_summarizer.services.search.intent import build_query_plan
    plan = build_query_plan(SearchIntent(raw_query='Compare verification and validation',
                           canonical_question='Compare verification and validation in computational fluid dynamics',
                           concepts=['verification', 'validation'], domain='computational fluid dynamics',
                           constraint_origin='model_proposed'))
    assert all('computational fluid dynamics' not in q for q in plan.openalex_lexical_variants)
    assert 'Proposed' in plan.domain_note
    for query in (plan.library_expanded, plan.openalex_semantic, plan.semantic_scholar, plan.openreview):
        assert 'computational fluid dynamics' not in query
        assert query == 'Compare verification and validation'


def test_broad_review_metadata_does_not_skip_specific_type_recovery(monkeypatch):
    from zotero_summarizer.services.search import _publication_types
    from zotero_summarizer.services.search._models import Candidate, QueryPlan
    candidate = Candidate(title='Evidence synthesis', doi='10.1234/example', publication_types=['review'])
    calls = []
    def recovered(query, **kwargs):
        calls.append(query)
        return [SimpleNamespace(doi='https://doi.org/10.1234/example', publication_types=['Systematic Review'])]
    monkeypatch.setattr(_publication_types, 'search_europepmc', recovered)
    monkeypatch.setattr(_publication_types, 'offline_requested', lambda: False)
    _publication_types.recover_types([candidate], QueryPlan(study_types=['systematic review'], constraint_origin='user_confirmed'))
    assert len(calls) == 1 and _publication_types.adequate_types(candidate, ['systematic review'])


def test_two_openalex_variants_cover_later_concept_and_acronym():
    from zotero_summarizer.services.search._models import SearchIntent
    from zotero_summarizer.services.search.intent import build_query_plan
    from zotero_summarizer.services.search.federate import _variant_queries
    plan = build_query_plan(SearchIntent(raw_query='Evidence methods', concepts=['one', 'two', 'three', 'four'],
                           synonyms=['EM'], constraint_origin='model_proposed'))
    executed = _variant_queries(plan.openalex_lexical_variants, plan.openalex_lexical, cap=2)
    assert len(executed) == 2
    assert '"four"' in executed[1] and '"EM"' in executed[1]


def test_only_observed_model_http_status_survives_metadata_projection():
    import httpx
    from zotero_summarizer.api.errors import APIError
    from zotero_summarizer.services.library._review_attempt import Attempt, observed_client
    request = httpx.Request('POST', 'https://private.invalid/?token=secret')
    response = httpx.Response(429, request=request)
    def unavailable(**kwargs):
        raise httpx.HTTPStatusError('provider throttled', request=request, response=response)
    with Attempt() as attempt:
        with pytest.raises(APIError):
            observed_client(SimpleNamespace(pydantic_prompt=unavailable), 'verifier').pydantic_prompt(prompt='source')
        from zotero_summarizer.services.library._deep_review_errors import failure_fields
        try:
            observed_client(SimpleNamespace(pydantic_prompt=unavailable), 'verifier').pydantic_prompt(prompt='source')
        except APIError as exc:
            fields = failure_fields(exc, None)
    assert fields['diagnostic']['http_status'] == 429
    assert fields['diagnostic']['stage'] == 'verification'
    assert 'private.invalid' not in str(attempt.metadata())
    assert any(e.get('identity', {}).get('http_status') == 429 for e in attempt.metadata()['events'])
