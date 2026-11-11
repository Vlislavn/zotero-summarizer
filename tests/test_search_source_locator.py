"""Synthetic transport/extraction, real acquisition/admission/session/DTO boundaries."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from tests._search_evidence_fixtures import QUOTE, journey, stored_session  # noqa: F401
from zotero_summarizer.integrations import europepmc, pdf_fetch
from zotero_summarizer.services.search import _fulltext, _question_evidence, federate, pipeline, session
from zotero_summarizer.services.search._models import Candidate
from zotero_summarizer.services.search._question_policy import current_binding, current_receipt, source_identity


@pytest.mark.parametrize('branch,pmcid', [('pmc', 'PMC123456'), ('pmc', '123456'), ('pdf', '')])
def test_selected_source_survives_real_snapshot_store_dto_reload_without_identity_change(journey, monkeypatch, tmp_path, branch, pmcid):
    for module in (_fulltext, _question_evidence, pipeline, session):
        assert Path(module.__file__).resolve().is_relative_to(Path(__file__).resolve().parents[1])
    research = stored_session(name='source-' + branch)
    candidate = research.candidates[0]
    research.candidates = [candidate]
    candidate.pmcid = pmcid
    candidate.doi = '10.1234/synthetic'
    candidate.url = 'https://example.org/bibliographic-record'
    original_identity = source_identity(candidate)
    expected = ('https://www.ebi.ac.uk/europepmc/webservices/rest/PMC123456/fullTextXML'
                if branch == 'pmc' else 'https://example.org/resolved-original.pdf')
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text='<article><body>' + QUOTE + '</body></article>'))
    client_type = httpx.Client
    monkeypatch.setattr(europepmc.httpx, 'Client', lambda **kwargs: client_type(transport=transport))
    monkeypatch.setattr(europepmc._RATE_LIMITER, 'acquire', lambda: None)
    fetched = Mock(return_value=tmp_path / 'synthetic-extraction.pdf')
    monkeypatch.setattr(pdf_fetch, 'fetch_pdf', fetched)
    monkeypatch.setattr(_fulltext, 'settings', lambda: SimpleNamespace(pdf_cache_dir=tmp_path))
    journey.deps.extractor = SimpleNamespace(extract_text=lambda path: QUOTE)
    journey.deps.unpaywall_client = SimpleNamespace(find_oa_pdf_url=lambda doi: expected)
    journey.verifier.state = 'sufficient'
    monkeypatch.setattr(_question_evidence, 'acquire_full_text', _fulltext.acquire_full_text)
    monkeypatch.setattr(pipeline, 'acquire_full_text', _fulltext.acquire_full_text)
    session.save(research)

    pipeline.run_review(research.id, deps=journey.deps)
    reloaded = session.load(research.id)
    retained = reloaded.candidates[0]
    row, = retained.question_evidence
    assert row['source_uri'] == expected
    assert reloaded.to_dict()['candidates'][0]['question_evidence'][0]['source_uri'] == expected
    assert json.loads((tmp_path / 'sessions' / (research.id + '.json')).read_text())['candidates'][0]['question_evidence'][0]['source_uri'] == expected
    assert retained.url == candidate.url and retained.identifier_keys() == candidate.identifier_keys()
    assert source_identity(retained) == original_identity
    assert current_receipt(row, retained, reloaded, current_binding())
    assert row['state'] == 'sufficient'
    assert all(QUOTE[span['start']:span['end']] == span['quote'] for span in row['evidence'])
    if branch == 'pdf':
        assert fetched.call_args.args[0] == expected != candidate.url


def test_pmcid_only_candidate_is_an_ordinary_provider_shape(monkeypatch):
    hit = europepmc._hit_from_result({'title': 'Synthetic PMC-only result', 'pmcid': 'PMC123456'})
    monkeypatch.setattr(federate, 'search_europepmc', lambda *args, **kwargs: [hit])
    candidate, = federate._europepmc_channel('synthetic', 1, '2026-10-29')
    assert candidate.to_dict()['pmcid'] == 'PMC123456'
    assert candidate.url == candidate.doi == candidate.pmid == ''


@pytest.mark.parametrize('failure', ['no_extractor', 'no_source', 'fetch_failed', 'empty_extraction'])
def test_unsuccessful_acquisition_clears_reused_outcome_and_keeps_string_api(monkeypatch, tmp_path, failure):
    monkeypatch.setattr(_fulltext, 'settings', lambda: SimpleNamespace(pdf_cache_dir=tmp_path))
    monkeypatch.setattr(pdf_fetch, 'resolve_pdf_url', lambda **kwargs: None if failure == 'no_source' else 'https://example.org/p.pdf')
    monkeypatch.setattr(pdf_fetch, 'fetch_pdf', lambda *args, **kwargs: None if failure == 'fetch_failed' else tmp_path / 'p.pdf')
    extractor = None if failure == 'no_extractor' else SimpleNamespace(extract_text=lambda path: '')
    outcome = {'source_uri': 'https://example.org/stale.pdf'}
    candidate = Candidate(title='Synthetic')
    assert _fulltext.acquire_full_text(candidate, extractor=extractor, outcome=outcome) == ''
    assert 'source_uri' not in outcome
    assert _fulltext.acquire_full_text(candidate, extractor=extractor) == ''
