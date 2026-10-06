"""Opt-in frozen real-input retention replay, not scientific or human acceptance.

Set ZS_REVIEW_ACCEPTANCE_MANIFEST to an explicitly frozen private manifest.
No manifest means SKIP, never PASS. Artifacts remain beside that private file.
Replay intentionally replaces cache identity only, preserving saved content.
Reference: ARE are/simulation/validation/tool_judge.py (HardToolJudge): explicit
oracle and hard checks. Diverge: no model judge; this contract is literal I/O.
"""
import copy
import hashlib
import json
import os
from pathlib import Path

import pytest

from tests._reading_contract import aggregate, canonical_text, check_html
from tests._real_review_manifest import digest, load_manifest, sentences
from tests.test_application_browser_live import application_browser  # noqa: F401
from zotero_summarizer.services._common import read_config
from zotero_summarizer.services.library import _paper_read_brief, _review_cache, _review_identity
from zotero_summarizer.storage import feeds
from zotero_summarizer.storage.feed_identity import stable_feed_key_from_parts

pytestmark = pytest.mark.skipif(
    not os.environ.get('ZS_REVIEW_ACCEPTANCE_MANIFEST'), reason='private frozen real-input manifest required',
)


def _save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2))
    path.chmod(0o600)


def _seed(settings, case):
    guid = 'frozen-retention-' + case['case_id']
    key = stable_feed_key_from_parts(guid=guid)
    with feeds.open_triage_conn(settings.triage_db_path) as conn:
        feeds.record_decision(conn, run_id='frozen-retention', decision=feeds.DECISION_USER_APPROVED,
                              feed_item={'feed_library_id': 1, 'item_id': 1, 'guid': guid,
                                         'title': case['title']}, composite_score=0)
        conn.commit()
    raw = copy.deepcopy(case['raw_entry'])
    raw['review_identity'] = _review_identity.build_review_identity(
        config=read_config(settings.config_path), pdf_path='', source_kind='override', focus_prompt='',
    )
    raw['review_contract_version'] = _review_cache.REVIEW_CONTRACT_VERSION
    _review_cache._write_one(key, raw)
    for field in ('digest', 'quality', 'goal_summaries'):
        assert raw[field] == case['raw_entry'][field]
    return key


# These annotations are confined to the isolated captured page, derived from
# actual DOM labels (never from oracle input). No production selectors are added.
ANNOTATE = '''() => {
 const norm = s => s.trim().replace(/\\s+/g, ' ');
 document.querySelectorAll('.goal-board-grid [role="meter"]').forEach(m => {
   const tile=m.parentElement;
   tile.setAttribute('data-contract-goal', norm(tile.firstElementChild.textContent));
   tile.children[1].setAttribute('data-contract-state', 'state');
   tile.querySelectorAll('blockquote').forEach(q=>q.setAttribute('data-contract-quote', norm(q.textContent)));
 });
 document.querySelectorAll('.review-reading li').forEach(li => {
   const label=li.querySelector(':scope > strong');
   if(label) li.setAttribute('data-contract-association', norm(label.textContent));
   const texts=Array.from(li.querySelectorAll('p')).map(p=>norm(p.textContent));
   if(texts.length) li.setAttribute('data-contract-finding', texts.sort((a,b)=>b.length-a.length)[0]);
 });
 document.querySelectorAll('.review-reading dt').forEach(dt => {
   dt.parentElement.setAttribute('data-contract-field', norm(dt.textContent));
 });
}'''


def _check(identity, path, selector, **options):
    return {'id': identity, 'source_id': tuple(path), 'selector': selector, **options}


def _field_checks(source):
    checks = []
    labels = {'executive_summary': 'Summary', 'read_why': 'Why read', 'read_parts': 'Read parts',
              'skip_parts': 'Skip parts', 'original_value': 'What the original adds',
              'relevance': 'Relevance', 'methods': 'Methods', 'limitations': 'Limitations',
              'controversies': 'Controversies', 'impact': 'Impact', 'industry_impact': 'Industry',
              'academy_impact': 'Academia', 'unknown_unknowns': 'Unknowns',
              'implementation': 'Implementation', 'key_strength': 'Strength', 'key_weakness': 'Weakness'}
    d = source['digest']
    labels['writing_reasons'] = 'Writing friction · ' + str(d.get('writing_friction') or '')
    for field, label in labels.items():
        value = d.get(field)
        if not value:
            continue
        values = list(enumerate(value)) if isinstance(value, list) else [(None, value)]
        for index, text in values:
            if not text:
                continue
            path = ('digest', field) + (() if index is None else (index,))
            options = {'scope': {'attrs': {'data-contract-field': label}}}
            if field == 'read_why':
                checks.append(_check(f'field-{field}-{index}', path, {'tag': 'main'}))
                continue
            if index is None:
                options['mode'] = 'exact'
            checks.append(_check(f'field-{field}-{index}', path, {'tag': 'dd'}, **options))
    for field, heading in [('tldr', 'Contribution'), ('key_findings', 'Findings and applicability')]:
        value = d.get(field)
        for index, text in (enumerate(value) if isinstance(value, list) else [(None, value)]):
            if text:
                path = ('digest', field) + (() if index is None else (index,))
                checks.append(_check(f'field-{field}-{index}', path, {'tag': 'main'}))
    p = d.get('parameters') or {}
    for field, label in [('dataset', 'Dataset / sample'), ('sample_size', 'Dataset / sample'),
                         ('architecture', 'Architecture'), ('baselines', 'Baselines'), ('metrics', 'Metrics')]:
        value = p.get(field)
        for index, text in (enumerate(value) if isinstance(value, list) else [(None, value)]):
            if text:
                path = ('digest', 'parameters', field) + (() if index is None else (index,))
                checks.append(_check(f'parameter-{field}-{index}', path, {'tag': 'dd'},
                                     scope={'attrs': {'data-contract-field': label}}))
    if p.get('external_validation') is not None:
        checks.append(_check('parameter-external-validation', ('digest', 'parameters', 'external_validation'),
                             {'tag': 'dd'}, scope={'attrs': {'data-contract-field': 'External validation'}},
                             mode='exact', expected='Yes' if p['external_validation'] else 'No'))
    if source['quality'].get('coverage_standard'):
        checks.append(_check('quality-coverage-standard', ('quality', 'coverage_standard'), {'tag': 'main'}))
    for field in ('red_flags', 'overstatements'):
        for index, text in enumerate(source['quality'].get(field) or []):
            checks.append(_check(f'quality-{field}-{index}', ('quality', field, index),
                                 {'tag': 'main'}))
    for field, text in (source['quality'].get('evidence') or {}).items():
        if text:
            checks.append(_check(f'quality-evidence-{field}', ('quality', 'evidence', field), {'tag': 'main'}))
    return checks


def _goal_checks(source, server=False):
    checks = []
    goals = source['goal_summaries']
    for index, goal in enumerate(goals):
        path = ('goal_summaries', index)
        scope = {'attrs': {'title' if server else 'data-contract-goal': goal['goal']}}
        checks.append(_check(f'goal-{index}-identity', path + ('goal',), scope, mode='count', expected=1))
        state = goal.get('retrieval_state') or 'not_retrieved'
        supported = state == 'hit' and goal.get('relevant') and not goal.get('abstained')
        label = ('○ abstained' if state == 'hit' and goal.get('abstained') else
                 '○ not supported' if state == 'hit' and not supported else
                 {'hit': '● addressed', 'miss': '○ not addressed', 'not_retrieved': '⚠ not retrieved'}[state])
        checks.append(_check(f'goal-{index}-state', path + ('retrieval_state',),
                             {'tag': 'div', 'attrs': {'class': 'g-state'} if server else {'data-contract-state': 'state'}},
                             scope=scope, expected=label, mode='exact'))
        for q, quote in enumerate(goal.get('supporting_quotes') or []):
            if state == 'hit' and quote:
                checks.append(_check(f'goal-{index}-quote-{q}', path + ('supporting_quotes', q),
                                     {'tag': 'div' if server else 'blockquote',
                                      'attrs': {'data-contract-quote': canonical_text('“' + quote.strip() + '”')}},
                                     scope=scope, expected='“' + quote.strip() + '”', mode='exact'))
        summary = goal.get('summary') or ''
        if summary:
            if not server:
                checks.append(_check(f'goal-{index}-original', path + ('summary',), {'tag': 'main'}))
            if state == 'hit':
                for s, sentence in enumerate(sentences(summary)):
                    labels = []
                    for other in goals:
                        if other.get('retrieval_state') == 'hit' and sentence in sentences(other.get('summary') or ''):
                            if other['goal'] not in labels:
                                labels.append(other['goal'])
                    assoc = 'For: ' + '; '.join(labels)
                    scope_f = {'tag': 'li', 'attrs': {'data-contract-association': assoc, 'data-contract-finding': canonical_text(sentence)}}
                    checks.append(_check(f'goal-{index}-finding-{s}', path + ('summary',),
                                         {'tag': 'p'}, scope=scope_f, expected=sentence))
    return checks


def _run(source, markup, checks, stem):
    html = stem.with_suffix('.html')
    _save(html, markup)
    receipts = check_html(source, markup, checks, evidence_path=html)
    outcome = aggregate([c['id'] for c in checks], receipts)
    _save(stem.with_suffix('.json'), {'outcome': outcome, 'receipts': receipts,
                                    'source_sha256': digest(source)})
    return outcome, receipts


def _server_markup(source):
    from bs4 import BeautifulSoup

    markup = _paper_read_brief._goal_board_html(source['goal_summaries'])
    soup = BeautifulSoup(markup, 'html.parser')
    for quote in soup.select('.g-quote'):
        quote['data-contract-quote'] = canonical_text(quote.get_text())
    for li in soup.select('li.goal-summary-item'):
        li['data-contract-association'] = ' '.join(li.strong.get_text().split())
        li['data-contract-finding'] = canonical_text(max((p.get_text() for p in li.find_all('p')), key=len))
    return str(soup)


def _negative_controls(page, source, checks, root):
    """Each mutation touches captured isolated DOM; exact named criterion must fail."""
    from bs4 import BeautifulSoup

    baseline = page.locator('html').evaluate('(e) => e.outerHTML')
    controls = []
    for prefix, actions in [('field-', ['remove', 'alter']),
                            ('goal-', ['remove', 'duplicate'])]:
        eligible = [c for c in checks if c['id'].startswith(prefix) and
                    (('scope' in c and c['selector'].get('tag') == 'dd') if prefix == 'field-'
                     else c['id'].endswith('-identity'))]
        for check in eligible[:2]:
            controls.extend((check, action) for action in actions)
    quote = next(c for c in checks if '-quote-' in c['id'])
    controls.extend([(quote, 'remove'), (quote, 'alter')])
    state = next(c for c in checks if c['id'].endswith('-state'))
    controls.append((state, 'alter'))
    association = next(c for c in checks if '-finding-' in c['id'])
    controls.append((association, 'drop-association'))
    legacy_field = next(c for c in checks if c['id'] == 'field-original_value-None')
    controls.append((legacy_field, 'remove'))
    controls.append((next(c for c in checks if c['id'] == 'field-executive_summary-None'), 'duplicate'))
    assert len(controls) >= 10
    outcomes = []
    for index, (check, action) in enumerate(controls):
        soup = BeautifulSoup(baseline, 'html.parser')
        scope = soup
        if 'scope' in check:
            scope = soup.find(attrs=check['scope'].get('attrs', {}))
        selector = check['selector']
        node = scope.find(selector.get('tag'), attrs=selector.get('attrs', {}))
        assert node is not None
        if action == 'drop-association':
            scope['data-contract-association'] = 'Changed goal association'
        elif action == 'remove':
            node.decompose()
        elif action == 'duplicate':
            node.insert_after(copy.copy(node))
        else:
            node.clear()
            node.append('Changed punctuation: !')
        # Real browser DOM mutation on an isolated page, not string-only grading.
        page.set_content(str(soup))
        markup = page.locator('html').evaluate('(e) => e.outerHTML')
        outcome, receipts = _run(source, markup, checks, root / f'negative-{index}')
        target = next(r for r in receipts if r['check_id'] == check['id'])
        assert target['status'] == 'FAIL', {'control': index, 'target': target}
        assert outcome['status'] == 'FAIL'
        outcomes.append({'criterion': check['id'], 'mutation': action, 'status': 'FAIL'})
    _save(root / 'negative-controls.json', outcomes)


@pytest.mark.parametrize('case_index', range(3))
@pytest.mark.parametrize('width', [390, 1024, 1440])
def test_frozen_real_review_retention(application_browser, case_index, width):
    from patchright.sync_api import expect

    manifest_path = Path(os.environ['ZS_REVIEW_ACCEPTANCE_MANIFEST'])
    frozen_bytes = manifest_path.read_bytes()
    manifest = load_manifest(manifest_path)
    case = manifest['cases'][case_index]
    page, client, settings, seen = application_browser
    key = _seed(settings, case)
    response = client.get('/api/golden/review-detail', params={'item_key': key})
    assert response.status_code == 200
    source = response.json()['deep_review']
    for field in ('quality', 'goal_summaries'):
        assert source[field] == case['raw_entry'][field]
    # The actual policy-projected API payload, not raw read_decision, is the oracle.
    root = manifest_path.parent / 'real-retention' / f'{case["case_id"]}-{width}'
    _save(root / 'source-binding.json', {'raw_entry_sha256': case['raw_entry_sha256'],
                                        'manifest_sha256': hashlib.sha256(frozen_bytes).hexdigest(),
                                        'source_cache_sha256': manifest['source_sha256'],
                                        'policy_projected_api': response.json()})
    page.set_viewport_size({'width': width, 'height': 900})
    page.goto(f'http://127.0.0.1/paper/{key}')
    expect(page.get_by_role('heading', name=case['title'], exact=True)).to_be_visible()
    expect(page.locator('.goal-board-grid [role="meter"]')).to_have_count(len(source['goal_summaries']))
    page.evaluate(ANNOTATE)
    checks = _field_checks(source) + _goal_checks(source)
    markup = page.locator('html').evaluate('(e) => e.outerHTML')
    outcome, receipts = _run(source, markup, checks, root / 'react')
    server_outcome, server_receipts = _run(source, _server_markup(source), _goal_checks(source, True), root / 'html-brief')
    failures = [r['check_id'] for r in receipts + server_receipts if r['status'] != 'PASS']
    # Capture every size from the same frozen input, including folded DOM content.
    layouts = []
    for size, zoom in [(width, 1), (320, 1), (640, 2)]:
        page.set_viewport_size({'width': size, 'height': 900})
        page.evaluate('(z) => document.documentElement.style.zoom = z', zoom)
        overflow = page.evaluate('''() => ({client:document.documentElement.clientWidth,
            scroll:document.documentElement.scrollWidth})''')
        stem = root / f'layout-{size}-{zoom}'
        page.screenshot(path=str(stem.with_suffix('.png')), full_page=True)
        stem.with_suffix('.png').chmod(0o600)
        layout_outcome, _ = _run(source, page.locator('html').evaluate('(e) => e.outerHTML'), checks, stem)
        layouts.append({'width': size, 'zoom': zoom, 'overflow': overflow, 'outcome': layout_outcome})
    _save(root / 'layouts.json', layouts)
    if case_index == 0 and width == 390:
        _negative_controls(page, source, checks, root)
    assert manifest_path.read_bytes() == frozen_bytes
    assert not [r for r in seen if r[0] != 'GET'], seen
    assert all(l['overflow']['scroll'] <= l['overflow']['client'] for l in layouts)
    assert all(l['outcome']['status'] == 'PASS' for l in layouts)
    assert outcome['status'] == server_outcome['status'] == 'PASS', failures
