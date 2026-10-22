"""Four opt-in built-UI cases over owned synthetic HTTP DTOs, not science.

Reuse the ASGI/Chromium sandbox, offline/disabled-AI policy and cleanup unchanged.
No real Search service, provider, Add, review, PDF or native-library work is run.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from tests import test_application_browser_live as harness
from tests import test_review_reading_browser as reader

application_browser = harness.application_browser
pytestmark = harness.pytestmark
ROOT = Path(__file__).resolve().parents[1]
ORIGIN = 'http://127.0.0.1'
QUESTION = 'Did the treatment improve the outcome?'
QUOTE = 'The treatment did not improve the outcome.'
CHANNEL_FIXTURE = 'frontend/src/components/paper/fixtures/discoveryChannelCandidates.json'
RECEIPT = {
    'candidate_id': 'answer', 'question_index': 0, 'question': QUESTION,
    'question_id': 'question-fingerprint', 'state': 'sufficient', 'reason': 'verified',
    'answer': 'No improvement was observed.', 'source_kind': 'acquired_original',
    'complete': False, 'source_id': 'original-identity', 'source_version_id': 'extraction-version',
    'source_sha256': 'source-fingerprint', 'generator_id': 'configured-feed-generator',
    'verifier_id': 'configured-deep-verifier', 'checked_scope': {'start': 0, 'end': 3000},
    'evidence': [{'passage_id': 'chars:1500:1600', 'start': 1500, 'end': 1600, 'quote': QUOTE}],
}


def _search_session(policy):
    # Same DTOs/states as Search.evidence.test.jsx; judgments are supplied fixtures.
    topic = {'candidate_id': 'topic', 'title': 'Top terminology match',
             'url': 'https://example.org/topic', 'query_score': 0.95}
    result = {'id': 'evidence', 'status': 'reviewed', 'questions': [QUESTION],
              'candidates': [topic]}
    if policy:
        result.update(evidence_policy='question_evidence', evidence_status={
            'state': 'complete', 'promoted_candidate_ids': ['answer'], 'topic_floor': 0.35})
        result['candidates'] += [
            {'candidate_id': 'answer', 'title': 'Lower-ranked answer',
             'url': 'https://example.org/answer', 'query_score': 0.5, 'relevance_band': 'weak',
             'question_evidence': [deepcopy(RECEIPT)]},
            {'candidate_id': 'partial', 'title': 'Partial', 'url': 'https://example.org/partial',
             'question_evidence': [{**deepcopy(RECEIPT), 'candidate_id': 'partial',
                                    'state': 'partial', 'uncovered': ['second outcome']}]},
            {'candidate_id': 'miss', 'title': 'Unchecked candidate', 'url': 'https://example.org/miss',
             'question_evidence': [{**deepcopy(RECEIPT), 'candidate_id': 'miss', 'state': 'unknown',
                                    'reason': 'unchecked_remainder', 'answer': None, 'evidence': []}]},
        ]
    return result


def _related():
    comments = {'availability': 'available', 'app_available': True, 'native_available': True,
                'used_for_ranking': False, 'used_context_ids': [], 'records': [
                    {'id': 'native:n1', 'text': 'Не подходит. Not useful.', 'source': 'native_note',
                     'authorship': 'uncertain', 'revision': 'clock'},
                    {'id': 'app:n1', 'text': 'Synthetic app context only.', 'source': 'app_note',
                     'authorship': 'user', 'revision': 1}]}
    item = {'item_key': 'PAPER001', 'title': 'Saved, already read paper', 'source_basis': 'abstract_only',
            'relationship': {'sentence': 'Both metadata records mention “graph retrieval”; '
                             'this is not confirmation or contradiction.'},
            'evidence': {'seed': {'field': 'abstract', 'start': 0, 'end': 15, 'excerpt': 'Graph retrieval'},
                         'candidate': {'field': 'title', 'start': 0, 'end': 15, 'excerpt': 'Graph retrieval'}},
            'comments': comments}
    return {'saved_seed': True, 'status': 'ready', 'items': [
        {**deepcopy(item), 'item_key': f'PAPER00{n + 1}',
         'title': item['title'] if n == 1 else f'Saved paper {n}'} for n in range(1, 5)],
        'seed_comments': {'availability': 'absent', 'records': [],
                          'app_available': True, 'native_available': True}}


def _detail(key):
    return {'item_key': key, 'title': 'Synthetic saved seed' if key == 'PAPER001' else 'Unsaved Today seed',
            'authors': [], 'tags': [], 'collections': [], 'has_pdf': False, 'questions': [],
            'user_note': 'Synthetic manual note',
            'verdict': {'id': 1, 'item_key': key, 'user_priority': 'must_read', 'comment': 'critical rationale'},
            'deep_review': {'model_read_decision': 'read', 'reading_policy_flags': ['abstract_only'],
                            'digest': {'basis': 'abstract', 'read_decision': 'skim',
                                       'read_why': 'A provisional abstract-only recommendation.',
                                       'read_parts': ['Figure 3 — compare site splits'],
                                       'skip_parts': ['Appendix A — notation'], 'estimated_read_minutes': 8,
                                       'original_value': 'The exact held-out protocol.'}}}


def _bindings():
    assert Path(harness.__file__).resolve() == ROOT / 'tests/test_application_browser_live.py'
    assert Path(reader.__file__).resolve() == ROOT / 'tests/test_review_reading_browser.py'
    assert Path(harness.create_app.__code__.co_filename).resolve() == ROOT / 'zotero_summarizer/api/app.py'
    names = ('tests/test_search_discovery_surfaces_browser.py', 'tests/test_application_browser_live.py',
             'tests/test_review_reading_browser.py', 'zotero_summarizer/api/app.py',
             'frontend/src/pages/Search.jsx', 'frontend/src/pages/Search.evidence.test.jsx',
             'frontend/src/components/search/QuestionEvidence.jsx', 'frontend/src/utils/searchSort.js',
             'frontend/src/components/paper/RelatedLibrary.jsx',
             'frontend/src/components/paper/RelatedLibrary.test.jsx', 'frontend/src/pages/reviewHelpers.js',
             'frontend/src/pages/PaperReviewPage.jsx', 'frontend/src/api/libraryApi.js',
             'frontend/src/api/searchApi.js', CHANNEL_FIXTURE)
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in names}


def _api_route(state):
    def route(intercept):
        request = intercept.request
        url = urlsplit(request.url)
        assert url.scheme == 'http' and url.netloc == '127.0.0.1', request.url
        actual = {'method': request.method, 'path': url.path, 'query': url.query,
                  'body': json.loads(request.post_data) if request.post_data else None}
        state['requests'].append(actual)
        if request.method == 'POST' and state['post'] is not None:
            expected, response = state['post']
            assert actual == expected, actual
            state['post'] = None
            state['posts'].append(actual)
            intercept.fulfill(status=200, json=response)
        elif request.method == 'GET' and (url.path, url.query) in state['gets']:
            intercept.fulfill(status=200, json=state['gets'][(url.path, url.query)])
        elif request.method == 'GET' and url.path in {
            '/api/setup/status', '/api/sync/pull', '/api/zotero/collections', '/api/zotero/tags',
        }:
            intercept.fallback()  # Existing local ASGI sandbox only, never a success catch-all.
        else:
            state['unexpected'].append(actual)
            intercept.abort()
            raise AssertionError(f'Unexpected surface request: {actual}')
    return route


def _check_served_build(state):
    for response in state['build_responses']:
        url = urlsplit(response.url)
        body = response.body()
        assert response.status == 200, response.url
        if response.request.resource_type == 'document':
            state['instrumented_documents'][url.path] = hashlib.sha256(body).hexdigest()
            continue
        source = (ROOT / 'frontend/dist' / url.path.lstrip('/')).resolve()
        assert source.is_relative_to(ROOT / 'frontend/dist') and source.is_file(), source
        assert body == source.read_bytes(), response.url
        state['served_build'][url.path] = hashlib.sha256(body).hexdigest()
    state['build_responses'].clear()
    assert state['instrumented_documents'], 'No browser document observed'
    assert any(path.endswith('.js') for path in state['served_build']), state['served_build']
    assert any(path.endswith('.css') for path in state['served_build']), state['served_build']


@pytest.fixture
def surface_browser(application_browser, request, record_property):
    page, _client, settings, seen = application_browser
    root = Path(os.environ.get('ZS_REVIEW_BROWSER_ARTIFACT_ROOT', settings.data_dir / 'review-browser'))
    artifact = root / f'surfaces-{request.node.name}'
    artifact.mkdir(parents=True, exist_ok=False, mode=0o700)
    document = _client.get('/')
    assert document.status_code == 200 and document.content == (ROOT / 'frontend/dist/index.html').read_bytes()
    state = {'page': page, 'artifact': artifact, 'requests': [], 'posts': [], 'post': None,
             'gets': {}, 'unexpected': [], 'served_build': {}, 'sources': _bindings(), 'asgi': seen,
             'build_responses': [], 'instrumented_documents': {},
             'asgi_index_sha256': hashlib.sha256(document.content).hexdigest()}
    for key in ('PAPER001', 'PAPER005'):
        state['gets'].update({
            ('/api/golden/review-detail', f'item_key={key}'): _detail(key),
            (f'/api/library/related/{key}', ''): _related() if key == 'PAPER001' else {
                'saved_seed': False, 'status': 'empty', 'items': []},
            (f'/api/library/render/{key}', ''): {'status': 'completed', 'figures': []},
            ('/api/library/deep-review/status', f'item_key={key}'): {'status': 'idle'},
        })
    state['gets'][('/api/admin/llm-reachability', '')] = {
        'stages': [{'stage': 'deep_review', 'enabled': False}]}

    def build_response(response):
        if (urlsplit(response.url).netloc == '127.0.0.1'
                and response.request.resource_type in {'document', 'script', 'stylesheet'}):
            state['build_responses'].append(response)

    page.route(ORIGIN + '/api/**', _api_route(state))
    page.on('response', build_response)
    record_property('surface_browser_artifact', str(artifact))
    try:
        yield state
        assert _bindings() == state['sources'], 'Surface sources changed during the case'
        assert state['post'] is None and not state['unexpected'], state
        _check_served_build(state)
    finally:
        receipt = {key: state[key] for key in ('requests', 'posts', 'unexpected', 'sources', 'served_build',
                                             'instrumented_documents', 'asgi_index_sha256', 'asgi')}
        receipt.update(case=request.node.nodeid, fixture_root=str(settings.data_dir),
                       boundary='Synthetic HTTP DTOs; actual built UI/ASGI assets, no scientific or provider qualification',
                       cleanup_owner='application_browser finally closes this context/browser and its routes/listeners')
        output = artifact / 'bindings-and-requests.json'
        output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
        output.chmod(0o600)


def _open(page, summary):
    from patchright.sync_api import expect

    expect(summary).to_have_count(1)
    assert summary.evaluate('e => e.tagName') == 'SUMMARY'
    assert not summary.evaluate('e => e.parentElement.open')
    summary.focus()
    page.keyboard.press('Tab')
    page.keyboard.press('Shift+Tab')
    expect(summary).to_be_focused()
    focus = summary.evaluate("e => ({visible:e.matches(':focus-visible'), outline:getComputedStyle(e).outlineStyle, shadow:getComputedStyle(e).boxShadow})")
    assert focus['visible'] and (focus['outline'] != 'none' or focus['shadow'] != 'none'), focus
    page.keyboard.press('Enter')
    assert summary.evaluate('e => e.parentElement.open')


def _capture(state, phase, selector, contrast_scopes):
    page, artifact = state['page'], state['artifact']
    page.mouse.move(0, 0)
    coverage = {scope: [row for row in page.evaluate(reader.CONTRAST, scope)
                        if page.locator(row['selector']).evaluate_all(
                            '(nodes, target) => nodes.some(node => node.matches(target))', target)]
                for scope, target in contrast_scopes.items()}
    rows = [row for selected in coverage.values() for row in selected]
    overflow = reader._no_overflow(page)
    image = artifact / f'{phase}.png'
    page.locator(selector).screenshot(path=str(image))
    image.chmod(0o600)
    output = artifact / f'{phase}.json'
    output.write_text(json.dumps({'contrast': rows, 'overflow': overflow,
                                 'scope': selector, 'contrast_scopes': contrast_scopes,
                                 'coverage': {scope: len(selected) for scope, selected in coverage.items()}},
                                indent=2), encoding='utf-8')
    output.chmod(0o600)
    assert all(coverage.values()), coverage
    assert rows and all(row['ratio'] >= 4.5 for row in rows), rows
    assert overflow['scroll'] <= overflow['client'], overflow


def _screen(state, policy):
    page = state['page']
    body = {'query': 'treatment studies', 'questions': [QUESTION]}
    if policy:
        body['evidence_policy'] = 'question_evidence'
    expected = {'method': 'POST', 'path': '/api/search/screen', 'query': '', 'body': body}
    state['post'] = (expected, _search_session(policy))
    with page.expect_response(ORIGIN + '/api/search/screen') as response:
        page.get_by_role('button', name='Search', exact=True).press('Enter')
    assert response.value.status == 200 and state['posts'][-1] == expected


def _card(page, title):
    return page.locator('div.border.rounded-xl').filter(has=page.get_by_role('link', name=title, exact=True))


def _search_evidence(page):
    from patchright.sync_api import expect

    card = _card(page, 'Lower-ranked answer')
    expect(card).to_be_visible()
    assert not card.evaluate("e => !!e.closest('details')"), 'Promoted weak result was collapsed'
    expect(card.get_by_text('weak match', exact=True)).to_be_visible()
    _open(page, card.locator('summary').filter(has_text='Question evidence'))
    expect(card.get_by_text('Sufficient evidence (AI-verified)', exact=True)).to_be_visible()
    expect(card.get_by_text(QUOTE, exact=True)).to_be_visible()
    expect(card.get_by_text('Extracted characters 1500–1600', exact=False)).to_be_visible()
    expect(card.get_by_text('extraction completeness is unknown', exact=False)).to_be_visible()
    _open(page, card.get_by_text('Source and evaluator provenance', exact=True))
    for value in ('original-identity', 'extraction-version / source-fingerprint',
                  'question-fingerprint', 'configured-feed-generator', 'configured-deep-verifier'):
        expect(card.get_by_text(value, exact=True)).to_be_visible()
    for title, label in (('Partial', 'Partial evidence'), ('Unchecked candidate', 'Unchecked')):
        other = _card(page, title)
        _open(page, other.locator('summary').filter(has_text='Question evidence'))
        expect(other.get_by_text(label, exact=True)).to_be_visible()
    expect(page.get_by_text('Still uncovered: second outcome', exact=True)).to_be_visible()
    expect(page.get_by_text('No answer located in retrieved passages; the rest is unchecked.', exact=True)).to_be_visible()
    expect(page.get_by_text('Not found in checked text', exact=True)).to_have_count(0)


@pytest.mark.parametrize('width', [390, 1440])
def test_built_search_explicit_policy_and_positive_evidence(surface_browser, width):
    from patchright.sync_api import expect

    state, page = surface_browser, surface_browser['page']
    page.set_viewport_size({'width': width, 'height': 900})
    page.goto(ORIGIN + '/search')
    expect(page.get_by_role('heading', name='Targeted Search', exact=True)).to_be_visible()
    choice = page.get_by_role('checkbox', name='Promote evidence-backed answers', exact=False)
    expect(choice).not_to_be_checked()
    expect(choice).to_be_disabled()
    assert state['posts'] == []
    page.get_by_role('textbox', name='Research topic', exact=True).fill('treatment studies')
    page.get_by_role('textbox', name='Questions', exact=True).fill(QUESTION)
    expect(choice).to_be_enabled()
    expect(choice).not_to_be_checked()
    _screen(state, False)
    expect(page.get_by_role('link', name='Top terminology match', exact=True)).to_be_visible()
    expect(choice).not_to_be_checked()
    choice.focus()
    page.keyboard.press('Space')
    expect(choice).to_be_checked()
    _screen(state, True)
    _search_evidence(page)
    expect(page.get_by_text('Experimental question evidence: 1 answer-bearing candidates promoted.', exact=False)).to_be_visible()
    sort = page.get_by_role('combobox', name='Sort by', exact=False)
    expect(sort).to_have_count(1)
    expect(sort).to_have_value('recommended')
    sort.select_option('question_evidence')
    expect(page.locator('div.border.rounded-xl a[target="_blank"]').first).to_have_text('Lower-ranked answer')
    assert page.locator('div.border.rounded-xl a[target="_blank"]').all_text_contents() == [
        'Lower-ranked answer', 'Top terminology match', 'Partial', 'Unchecked candidate']
    _capture(state, 'positive-evidence', 'main > .max-w-3xl', {
        'main > .max-w-3xl form > label': '*',
        'main > .max-w-3xl > div:has(> [role="status"])': '[role="status"]',
        'main > .max-w-3xl .border.rounded-xl details': '*',
    })
    assert len(state['posts']) == 2
    assert not [row for row in state['requests'] if 'materialize' in row['path'] or row['path'].endswith('/review')]


def _discovery(availability):
    candidates = json.loads((ROOT / CHANNEL_FIXTURE).read_text(encoding='utf-8'))
    candidates[0]['in_library'] = False
    candidates += [
        {'candidate_id': 'unsafe-with-doi', 'title': 'Unsafe URL with a valid identifier fallback',
         'url': 'javascript:alert(1)', 'doi': '10.1234/safe-fallback'},
        {'candidate_id': 'unsupported', 'title': 'No supported identifier or address', 'url': 'data:text/html,unsafe',
         'pmid': '../not-a-pmid', 'openalex_id': 'https://example.org/W123'},
    ]
    outcomes = [{'provider': 'arxiv', 'variant': 0, 'status': 'success_empty', 'count': 0, 'error_code': None}]
    if availability == 'partial_failure':
        outcomes += [{'provider': 'semantic_scholar', 'variant': 0, 'status': 'failed',
                      'count': None, 'error_code': 'rate_limited'}]
    elif availability == 'unknown':
        outcomes[0].update(status='unknown', count=None)
    return {'status': 'screened', 'candidates': candidates if availability == 'partial_failure' else [],
            'discovery': {'provider_availability': availability, 'source_outcomes': outcomes}}


def _find(state, availability):
    page = state['page']
    query = 'refresh=true' if state['posts'] else ''
    expected = {'method': 'POST', 'path': '/api/library/related/PAPER001/discover', 'query': query, 'body': None}
    state['post'] = (expected, _discovery(availability))
    with page.expect_response(ORIGIN + expected['path'] + ('?' + query if query else '')) as response:
        page.get_by_role('button', name='Find new relevant', exact=True).press('Enter')
    assert response.value.status == 200 and state['posts'][-1] == expected


def _related_evidence(page):
    from patchright.sync_api import expect

    related = page.locator('#related-library')
    expect(related.locator('article')).to_have_count(3)
    expect(related.get_by_text('Saved paper 4', exact=True)).to_have_count(0)
    first = related.locator('article').first
    expect(first.get_by_role('link', name='Saved, already read paper', exact=True)).to_be_visible()
    expect(first.get_by_text('this is not confirmation or contradiction.', exact=False)).to_be_visible()
    _open(page, first.get_by_text('Evidence · abstract only', exact=True))
    expect(first.get_by_text('Public metadata only; original full-text relationships have not been assessed.', exact=True)).to_be_visible()
    assert first.locator('details').first.locator('blockquote').all_text_contents() == [
        'seed · abstract · characters 0–15Graph retrieval', 'candidate · title · characters 0–15Graph retrieval']
    _open(page, first.get_by_text('Saved paper comments — available', exact=True))
    for text in ('Не подходит. Not useful.', 'Synthetic app context only.',
                 'native note · authorship uncertain', 'app note · user text',
                 'Context only, not used for ranking or as evidence of a paper relationship.'):
        expect(first.get_by_text(text, exact=True)).to_be_visible()
    expect(related.get_by_text('semantic intent is unqualified', exact=False)).to_be_visible()
    expect(related.get_by_role('checkbox')).to_have_count(0)


def _safe_discovery_links(related):
    from patchright.sync_api import expect

    candidates = json.loads((ROOT / CHANNEL_FIXTURE).read_text(encoding='utf-8'))
    for title, url in [
        (candidates[0]['title'], 'https://doi.org/10.1234/fixture-doi'),
        (candidates[1]['title'], 'https://pubmed.ncbi.nlm.nih.gov/12345678/'),
        (candidates[2]['title'], 'https://openalex.org/W1234567890'),
        ('Unsafe URL with a valid identifier fallback', 'https://doi.org/10.1234/safe-fallback'),
    ]:
        link = related.get_by_role('link', name=title, exact=True)
        expect(link).to_be_visible()
        expect(link).to_have_attribute('href', url)
    expect(related.get_by_role('link', name='No supported identifier or address', exact=True)).to_have_count(0)
    expect(related.get_by_text('No supported link available.', exact=True)).to_be_visible()
    assert all(url.startswith(('http://', 'https://')) for url in related.locator('a[target="_blank"]').evaluate_all(
        "links => links.map(link => link.getAttribute('href'))"))
    expect(related.get_by_text('Not saved (identifier checked)', exact=False)).to_be_visible()
    expect(related.get_by_text('Saved-library membership unknown — not confirmed new', exact=False).first).to_be_visible()


def _preserved_rail(page, note='Synthetic manual note'):
    from patchright.sync_api import expect

    for name in ('Your verdict', 'My notes'):
        expect(page.get_by_role('heading', name=name, exact=True)).to_have_count(1)
        expect(page.get_by_role('heading', name=name, exact=True)).to_be_visible()
    figures = page.locator('section#figures')
    expect(figures).to_have_count(1)
    expect(figures).to_be_visible()
    expect(figures.get_by_text('Figures', exact=True)).to_be_visible()
    expect(page.get_by_role('region', name='Reading plan')).to_be_visible()
    expect(page.get_by_text('Figure 3 — compare site splits', exact=True)).to_be_visible()
    expect(page.get_by_role('textbox', name='Question about this paper', exact=True)).to_be_visible()
    expect(page.get_by_role('textbox', name='Manual notes', exact=True)).to_have_value(note)


@pytest.mark.parametrize('width', [390, 1440])
def test_built_related_positive_context_and_explicit_discovery(surface_browser, width):
    from patchright.sync_api import expect

    state, page = surface_browser, surface_browser['page']
    page.set_viewport_size({'width': width, 'height': 900})
    page.goto(ORIGIN + '/paper/PAPER001')
    expect(page.get_by_role('heading', name='Synthetic saved seed', exact=True)).to_be_visible()
    _preserved_rail(page)
    _related_evidence(page)
    related = page.locator('#related-library')
    find = related.get_by_role('button', name='Find new relevant', exact=True)
    expect(find).to_be_enabled()
    assert state['posts'] == []
    notes = page.get_by_role('textbox', name='Manual notes', exact=True)
    notes.fill('Unsaved synthetic draft')
    _find(state, 'partial_failure')
    expect(related.get_by_text('Partial provider failure — available results are retained.', exact=False)).to_be_visible()
    expect(related.get_by_text('No unsaved candidates returned', exact=False)).to_have_count(0)
    _safe_discovery_links(related)
    _open(page, related.get_by_text('Source outcomes', exact=True))
    expect(related.get_by_text('arxiv · pass 1 · success empty · 0 returned', exact=True)).to_be_visible()
    expect(related.get_by_text('semantic_scholar · pass 1 · failed · rate limited', exact=True)).to_be_visible()
    expect(related.get_by_text('Discovery only — nothing added or reviewed.', exact=False)).to_be_visible()
    expect(related.get_by_role('button', name='Add to library', exact=True)).to_have_count(0)
    _capture(state, 'positive-related-and-discovery', '#related-library', {'#related-library': '*'})
    _find(state, 'unknown')
    expect(related.get_by_text('Some provider outcomes are unknown', exact=False)).to_be_visible()
    expect(related.get_by_text('No unsaved candidates returned in this search. This is not proof that none exist.', exact=True)).to_be_visible()
    expect(related.get_by_text('Partial provider failure', exact=False)).to_have_count(0)
    _capture(state, 'unknown-empty', '#related-library', {'#related-library': '*'})
    _find(state, 'observed')
    expect(related.get_by_text('Source availability: observed.', exact=False)).to_be_visible()
    expect(related.get_by_text('Some provider outcomes are unknown', exact=False)).to_have_count(0)
    expect(related.get_by_text('No unsaved candidates returned', exact=False)).to_be_visible()
    _capture(state, 'observed-empty', '#related-library', {'#related-library': '*'})
    _preserved_rail(page, note='Unsaved synthetic draft')
    dialogs = []

    def dismiss(dialog):
        dialogs.append(dialog.message)
        dialog.dismiss()

    page.once('dialog', dismiss)
    related.get_by_role('link', name='Saved paper 2', exact=True).press('Enter')
    expect(page).to_have_url(ORIGIN + '/paper/PAPER001')
    assert dialogs == ['You have unsaved notes on this paper. Leave without saving them?']
    expect(notes).to_have_value('Unsaved synthetic draft')
    notes.fill('Synthetic manual note')
    _check_served_build(state)  # Response bodies retire on cross-document navigation.
    page.goto(ORIGIN + '/paper/PAPER005')
    expect(page.get_by_role('heading', name='Unsaved Today seed', exact=True)).to_be_visible()
    expect(page.get_by_text('No related saved papers returned by this library search.', exact=True)).to_be_visible()
    expect(page.get_by_role('button', name='Find new relevant', exact=True)).to_have_count(0)
    assert len(state['posts']) == 3
    assert all(row['path'] == '/api/library/related/PAPER001/discover' for row in state['posts'])
