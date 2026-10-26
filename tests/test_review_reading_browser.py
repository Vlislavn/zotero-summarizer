"""Owned synthetic producer → API → built-browser receipts; not live semantic/native acceptance.

ZS_REVIEW_BROWSER_ARTIFACT_ROOT explicitly selects a private persistent directory.
Otherwise receipts are transient, beneath the isolated Settings.data_dir.
"""
import json
import os
from pathlib import Path

import pytest

from tests.test_application_browser_live import application_browser  # noqa: F401
from tests.test_research_feed import _review
from zotero_summarizer.services._common import read_config
from zotero_summarizer.services.library import _review_cache, _review_identity
from zotero_summarizer.storage import feeds
from zotero_summarizer.storage.feed_identity import stable_feed_key_from_parts

pytestmark = pytest.mark.skipif(
    os.environ.get('ZS_APPLICATION_BROWSER_SMOKE') != '1', reason='opt-in real built Chromium',
)


_ORIGINAL = ('Synthetic contribution. Synthetic main finding. Synthetic methods. Synthetic limitations.\n'
             '§2: Synthetic method details.\n§4.2 Held-out evaluation — compare site splits.\n'
             'Figure 3 — inspect failure cases.\nAppendix A — notation recap.\n'
             + 'SyntheticLongUnbrokenMethod' * 30)


def _publish_current(key, entry):
    accepted = _review(key, source_text=_ORIGINAL, digest=entry['digest'],
                       quality=entry['quality'], goal_summaries=entry['goal_summaries'])
    _review_cache._write_one(key, accepted)


def _seed(settings, variant):
    guid = f'synthetic-reading-{variant}'
    key = stable_feed_key_from_parts(guid=guid)
    with feeds.open_triage_conn(settings.triage_db_path) as conn:
        feeds.record_decision(conn, run_id='synthetic-reading', decision=feeds.DECISION_USER_APPROVED,
                              feed_item={'feed_library_id': 1, 'item_id': 1, 'guid': guid,
                                         'title': f'Synthetic reading {variant}'}, composite_score=4.5)
        conn.commit()
    digest = {'tldr': 'Synthetic contribution.', 'read_decision': 'skim', 'basis': 'full_text',
              'writing_friction': 'low', 'writing_reasons': [], 'read_parts': ['§2 — Synthetic methods'],
              'skip_parts': [], 'estimated_read_minutes': 5, 'original_value': 'Synthetic original method details.',
              'read_why': 'Synthetic reading rationale.', 'key_findings': ['Synthetic main finding.'],
              'methods': 'Synthetic methods.', 'limitations': 'Synthetic limitations.',
              'key_weakness': 'Synthetic weakness.', 'executive_summary': 'Synthetic reference summary.'}
    quality = {'grade': 'B', 'basis': 'full_text', 'quality_band': 'neutral', 'coverage_met': 1,
               'coverage_applicable': 2, 'coverage_standard': 'Synthetic standard',
               'red_flags': ['Synthetic caveat.'], 'rubric': {'external_validation': 'no'}}
    goals = [{'goal': 'Synthetic evaluation', 'retrieval_state': 'hit', 'relevant': True, 'abstained': False,
              'score': 2, 'summary': 'Synthetic goal finding.',
              'supporting_quotes': ['Synthetic evidence quote.']}]
    if variant == 'long-incomplete':
        digest['tldr'] = 'Synthetic long contribution. ' * 40
        digest['methods'] = 'SyntheticLongUnbrokenMethod' * 30
        goals.append({'goal': 'Synthetic unavailable goal', 'retrieval_state': 'not_retrieved'})
    if variant == 'legacy':
        digest, goals = {}, []
        quality = {'grade': 'B', 'verdict': 'Synthetic legacy assessment.'}
    identity = _review_identity.build_review_identity(
        config=read_config(settings.config_path), pdf_path='', source_kind='override', focus_prompt='',
    )
    entry = {'digest': digest, 'quality': quality, 'goal_summaries': goals,
             'review_contract_version': _review_cache.REVIEW_CONTRACT_VERSION, 'review_identity': identity}
    if variant == 'legacy':
        _review_cache._write_one(key, entry)
    else:
        _publish_current(key, entry)
    return key


CONTRAST = """scope => {
 scope ??= '.paper-reading-page main';
 const rgb = s => (s.match(/[\\d.]+/g)||[]).map(Number);
 const blend = (a,b) => a.slice(0,3).map((v,i)=>v*(a[3]??1)+b[i]*(1-(a[3]??1)));
 const lum = a => a.map(v=>v/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4)
   .reduce((s,v,i)=>s+v*[.2126,.7152,.0722][i],0);
 const selector = e => {const root=e.closest(scope);let parts=[]; while(e && e!==root) {
   parts.unshift(e.tagName.toLowerCase()+':nth-child('+(Array.from(e.parentElement.children).indexOf(e)+1)+')');
   e=e.parentElement;} return scope+' > '+parts.join(' > ');};
 const elements = Array.from(document.querySelectorAll(scope+' *')).filter(e=>
   e.checkVisibility() && Array.from(e.childNodes).some(n=>n.nodeType===3&&n.textContent.trim())
 );
 if (!elements.length) throw new Error('Contrast probe matched no visible text');
 return elements.map(e=>{let chain=[],p=e; while(p){chain.unshift(p);p=p.parentElement;}
   let bg=[255,255,255]; for(const n of chain) bg=blend(rgb(getComputedStyle(n).backgroundColor),bg);
   const s=getComputedStyle(e),fg=blend(rgb(s.color),bg),a=lum(fg),b=lum(bg);
   return {selector:selector(e),text:e.textContent.trim().slice(0,80),color:s.color,
     background:bg,fontSize:s.fontSize,ratio:(Math.max(a,b)+.05)/(Math.min(a,b)+.05)};
 });
}"""


@pytest.mark.parametrize('width', [390, 1440])
def test_retained_review_flag_does_not_claim_native_note_delivery(application_browser, width):
    from patchright.sync_api import expect

    page, client, settings, seen = application_browser
    key = _seed(settings, f'legacy-note-claim-{width}')
    entry = _review_cache._read_all()[key]
    entry.update(zotero_note_written=True, reviewed_at='2026-10-14T00:00:00Z')
    _review_cache._write_one(key, entry)
    before = _review_cache._cache_path().read_bytes()
    payload = client.get('/api/golden/review-detail', params={'item_key': key}).json()['deep_review']
    assert payload['zotero_note_written'] is True
    page.set_viewport_size({'width': width, 'height': 900})
    page.goto(f'http://127.0.0.1/paper/{key}')
    expect(page.get_by_role('heading', name='Contribution', exact=True)).to_be_visible()
    expect(page.get_by_text('saved to Zotero ✓', exact=True)).to_have_count(0)
    expect(page.locator('span[title]').filter(has_text='reviewed ')).to_have_count(1)
    colors, overflow = page.evaluate(CONTRAST), _no_overflow(page)
    assert colors and all(row['ratio'] >= 4.5 for row in colors)
    assert overflow['scroll'] <= overflow['client']
    assert _review_cache._cache_path().read_bytes() == before
    assert not any(method != 'GET' for method, _path, _status in seen), seen
    root = Path(os.environ.get('ZS_REVIEW_BROWSER_ARTIFACT_ROOT', settings.data_dir / 'review-browser'))
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    (root / f'legacy-note-claim-{width}.json').write_text(json.dumps({
        'colors': colors, 'overflow': overflow, 'requests': seen,
        'retained_flag': payload['zotero_note_written'], 'cache_bytes_unchanged': True,
    }, indent=2))
    page.screenshot(path=str(root / f'legacy-note-claim-{width}.png'), full_page=True)


def test_contrast_probe_covers_default_and_explicit_scope_and_refuses_empty(application_browser):
    from patchright.sync_api import Error, expect

    page, _client, settings, _seen = application_browser
    key = _seed(settings, 'full')
    page.goto(f'http://127.0.0.1/paper/{key}')
    expect(page.get_by_role('heading', name='Contribution', exact=True)).to_be_visible()
    default = page.evaluate(CONTRAST)
    explicit = page.evaluate(CONTRAST, '.paper-reading-page main')
    assert default
    assert default == explicit
    with pytest.raises(Error, match='Contrast probe matched no visible text'):
        page.evaluate(CONTRAST, '#missing-contrast-control')


def _no_overflow(page):
    receipt = page.evaluate('''() => ({width:innerWidth, client:document.documentElement.clientWidth,
        scroll:document.documentElement.scrollWidth})''')
    return receipt


def _keyboard_disclosure(page, label, *, exact=False):
    from patchright.sync_api import expect

    summary = page.get_by_text(label, exact=True) if exact else page.locator('summary').filter(has_text=label)
    expect(summary).to_have_count(1)
    assert summary.evaluate("e => e.tagName") == 'SUMMARY'
    assert not summary.evaluate('(e) => e.parentElement.open')
    summary.focus()
    page.keyboard.press('Tab')
    page.keyboard.press('Shift+Tab')
    expect(summary).to_be_focused()
    focus = summary.evaluate('''e => ({visible:e.matches(':focus-visible'),
        outline:getComputedStyle(e).outlineStyle, shadow:getComputedStyle(e).boxShadow})''')
    assert focus['visible'] and (focus['outline'] != 'none' or focus['shadow'] != 'none'), focus
    page.keyboard.press('Enter')
    assert summary.evaluate('(e) => e.parentElement.open')


def _full_contract(page):
    from patchright.sync_api import expect

    headings = ['Contribution', 'Caveats and coverage', 'Findings and applicability',
                'Methods and limitations', 'Assessment and reference']
    assert page.locator('.review-reading h2').all_text_contents() == headings
    for heading in headings:
        expect(page.get_by_role('heading', name=heading, exact=True)).to_be_visible()
    caveat = page.get_by_text('Synthetic caveat.', exact=True)
    expect(caveat).to_be_visible()
    assert not caveat.evaluate("e => !!e.closest('details')")
    expect(page.get_by_role('meter', name='Synthetic evaluation relevance')).to_be_visible()
    expect(page.get_by_text('Synthetic main finding.', exact=True)).to_have_count(1)
    expect(page.get_by_text('Synthetic reading rationale.', exact=True)).to_have_count(1)
    _keyboard_disclosure(page, 'Full digest and assessment')
    expect(page.get_by_text('Synthetic reference summary.', exact=True)).to_be_visible()
    _keyboard_disclosure(page, 'evidence', exact=True)
    expect(page.get_by_text('“Synthetic evidence quote.”', exact=True)).to_be_visible()


@pytest.mark.parametrize('variant', ['full', 'long-incomplete', 'legacy'])
@pytest.mark.parametrize('width', [390, 1024, 1440])
def test_real_built_review_reading(application_browser, variant, width):
    from patchright.sync_api import expect

    page, client, settings, seen = application_browser
    key = _seed(settings, variant)
    response = client.get('/api/golden/review-detail', params={'item_key': key})
    assert response.status_code == 200, response.text
    if variant == 'legacy':
        assert response.json()['deep_review'] is None
    else:
        assert response.json()['deep_review']['quality']['grade'] == 'B'
    page.set_viewport_size({'width': width, 'height': 900})
    page.goto(f'http://127.0.0.1/paper/{key}')
    expect(page.get_by_role('heading', name=f'Synthetic reading {variant}', exact=True)).to_be_visible()
    if variant == 'full':
        _full_contract(page)
    elif variant == 'legacy':
        expect(page.get_by_text('Quality B', exact=True)).to_have_count(0)
        expect(page.get_by_text('Synthetic legacy assessment.', exact=True)).to_have_count(0)
        expect(page.get_by_role('heading', name='Contribution', exact=True)).to_have_count(0)
    else:
        expect(page.get_by_role('heading', name='Contribution', exact=True)).to_be_visible()
    root = Path(os.environ.get('ZS_REVIEW_BROWSER_ARTIFACT_ROOT', settings.data_dir / 'review-browser'))
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    stem = root / f'{variant}-{width}'
    page.screenshot(path=str(stem.with_suffix('.png')), full_page=True)
    stem.with_suffix('.png').chmod(0o600)
    receipt = {'variant': variant, 'width': width, 'default': _no_overflow(page),
               'contrast': page.evaluate(CONTRAST), 'requests': seen}
    page.set_viewport_size({'width': 640, 'height': 900})
    font_style = page.add_style_tag(content='html {font-size:200% !important;}')
    receipt['font-200-at-640'] = _no_overflow(page)
    font_style.evaluate('(e) => e.remove()')
    for size, zoom in [(320, 1), (640, 2)]:
        page.set_viewport_size({'width': size, 'height': 900})
        page.evaluate('(z) => document.documentElement.style.zoom = z', zoom)
        if zoom == 2:
            page.add_style_tag(content='''html {font-size: 100% !important;}
            .paper-reading-page * {line-height:1.5 !important;letter-spacing:.12em !important;
            word-spacing:.16em !important;} .paper-reading-page p {margin-bottom:2em !important;}''')
        receipt[f'reflow-{size}'] = _no_overflow(page)
    stem.with_suffix('.json').write_text(json.dumps(receipt, indent=2))
    stem.with_suffix('.json').chmod(0o600)
    failures = [r for r in receipt['contrast'] if r['ratio'] < 4.5]
    print('READING_BROWSER_RECEIPT', json.dumps({'artifact': str(stem), 'contrast_failures': failures}))
    overflow = {k: v for k, v in receipt.items() if isinstance(v, dict)
                and 'scroll' in v and v['scroll'] > v['client']}
    assert not overflow and not failures, {'overflow': overflow, 'contrast': failures}


@pytest.mark.parametrize('variant', ['high-friction', 'weak-evidence', 'legacy-unknown'])
@pytest.mark.parametrize('width', [390, 1440])
def test_three_signals_in_built_review(application_browser, variant, width):
    from patchright.sync_api import expect

    page, client, settings, _ = application_browser
    key = _seed(settings, f'signals-{variant}')
    entry = _review_cache._read_all()[key]
    entry['digest'].update(read_decision='read', basis='full_text', novelty=5, significance=4,
                           writing_friction='low', writing_reasons=[])
    entry['quality'].update(quality_band='highlight', paper_type='position',
                            coverage_standard='Argument quality', red_flags=[],
                            rubric={'thesis': 'yes', 'evidence_cited': 'yes'})
    entry['goal_summaries'][0]['abstained'] = False
    reason = 'Synthetic §2 uses the central construct before defining it.'
    if variant == 'high-friction':
        entry['digest'].update(writing_friction='high', writing_reasons=[reason])
    elif variant == 'weak-evidence':
        entry['quality'].update(quality_band='flag', rubric={'thesis': 'yes', 'evidence_cited': 'no'})
    else:
        for field in ['basis', 'writing_friction', 'writing_reasons']:
            entry['digest'].pop(field)
    if variant == 'legacy-unknown':
        _review_cache._write_one(key, entry)
    else:
        _publish_current(key, entry)
    payload = client.get('/api/golden/review-detail', params={'item_key': key}).json()['deep_review']
    if variant == 'legacy-unknown':
        assert payload is None
        page.set_viewport_size({'width': width, 'height': 900})
        page.goto(f'http://127.0.0.1/paper/{key}')
        expect(page.get_by_role('heading', name=f'Synthetic reading signals-{variant}', exact=True)).to_be_visible()
        expect(page.get_by_role('group', name='Idea, evidence and writing')).to_have_count(0)
        return
    assert payload['digest']['read_decision'] == 'skim'
    assert payload['model_read_decision'] == 'read'
    page.set_viewport_size({'width': width, 'height': 900})

    page.goto(f'http://127.0.0.1/paper/{key}')

    signals = page.get_by_role('group', name='Idea, evidence and writing')
    expect(signals).to_be_visible()
    expect(signals.locator('summary')).to_contain_text('Idea: high')
    if variant == 'high-friction':
        expect(signals.locator('summary')).to_contain_text('Evidence: Highlight · Writing: high friction')
        expect(signals.get_by_text('Idea preserved', exact=False)).to_be_visible()
        expect(signals.get_by_text(reason, exact=True)).not_to_be_visible()
    elif variant == 'weak-evidence':
        expect(signals.locator('summary')).to_contain_text('Evidence: weak (Flag) · Writing: low friction')
        expect(signals.get_by_text('Concept/hypothesis source', exact=False)).to_be_visible()
    _keyboard_disclosure(page, 'Idea: high')
    expect(signals.get_by_text('Position / perspective · Argument quality.', exact=True)).to_be_visible()
    expect(signals.get_by_text('Automatic policy capped', exact=False)).to_be_visible()
    if variant == 'high-friction':
        expect(page.get_by_text(reason, exact=True)).to_have_count(1)
        expect(signals.get_by_text(reason, exact=True)).to_be_visible()
    expect(page.get_by_text('Ablations isolate what drives the result', exact=True)).to_have_count(0)
    overflow = _no_overflow(page)
    assert overflow['scroll'] <= overflow['client'], overflow
    root = Path(os.environ.get('ZS_REVIEW_BROWSER_ARTIFACT_ROOT', settings.data_dir / 'review-browser'))
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    screenshot = root / f'signals-{variant}-{width}.png'
    page.screenshot(path=str(screenshot), full_page=True)
    screenshot.chmod(0o600)


SIGNAL_CONTRAST = """root => {
 const rgb = s => (s.match(/[\\d.]+/g)||[]).map(Number);
 const blend = (a,b) => a.slice(0,3).map((v,i)=>v*(a[3]??1)+b[i]*(1-(a[3]??1)));
 const lum = a => a.map(v=>v/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4)
   .reduce((s,v,i)=>s+v*[.2126,.7152,.0722][i],0);
 return Array.from(root.querySelectorAll('summary > span, dt')).filter(e=>e.checkVisibility())
   .map(e=>{let chain=[],p=e; while(p){chain.unshift(p);p=p.parentElement;}
     let bg=[255,255,255]; for(const n of chain) bg=blend(rgb(getComputedStyle(n).backgroundColor),bg);
     const s=getComputedStyle(e),fg=blend(rgb(s.color),bg),a=lum(fg),b=lum(bg);
     return {tag:e.tagName,text:e.textContent.trim(),color:s.color,background:bg,fontSize:s.fontSize,
       ratio:(Math.max(a,b)+.05)/(Math.min(a,b)+.05)};
   });
}"""


@pytest.mark.parametrize('surface', ['compact', 'nonflat'])
@pytest.mark.parametrize('decision', ['read', 'skim', 'skip'])
@pytest.mark.parametrize('width', [390, 1440])
def test_three_signal_contrast_outside_full_reader(application_browser, surface, decision, width):
    from patchright.sync_api import expect

    page, client, settings, _ = application_browser
    variant = f'contrast-{surface}-{decision}-{width}'
    key = _seed(settings, variant)
    entry = _review_cache._read_all()[key]
    entry['digest'].update(read_decision='read' if decision == 'skim' else decision,
                           basis='full_text',
                           novelty=5, significance=4, writing_friction='low', writing_reasons=[])
    entry['quality'].update(quality_band='neutral' if decision == 'skim' else 'highlight', red_flags=[])
    entry['goal_summaries'][0]['abstained'] = False
    _publish_current(key, entry)
    response = client.get('/api/golden/review-detail', params={'item_key': key})
    assert response.status_code == 200, response.text
    assert response.json()['deep_review']['digest']['read_decision'] == decision
    page.set_viewport_size({'width': width, 'height': 900})
    page.goto('http://127.0.0.1/')
    page.get_by_role('button', name='Skip for now', exact=True).click()
    if surface == 'compact':
        page.get_by_role('button').filter(has_text=f'Synthetic reading {variant}').click()
    else:
        page.goto(f'http://127.0.0.1/library?mode=batch&item_key={key}')

    signals = page.get_by_role('group', name='Idea, evidence and writing')
    expect(signals).to_be_visible()
    assert not signals.evaluate("e => !!e.closest('.review-reading, .paper-reading-page')")
    page.mouse.move(0, 0)
    closed = signals.evaluate(SIGNAL_CONTRAST)
    assert len(closed) == 1 and closed[0]['tag'] == 'SPAN', closed
    assert closed[0]['ratio'] >= 4.5, closed
    _keyboard_disclosure(page, 'Idea: high')
    for label in ['Idea value', 'Scientific quality', 'Writing friction · low']:
        expect(signals.locator('dt').filter(has_text=label)).to_be_visible()
    expanded = signals.evaluate(SIGNAL_CONTRAST)
    labels = [row for row in expanded if row['tag'] == 'DT']
    assert len(labels) == (4 if decision == 'skim' else 3), expanded
    assert all(row['ratio'] >= 4.5 for row in expanded), expanded
    overflow = _no_overflow(page)
    assert overflow['scroll'] <= overflow['client'], overflow
    root = Path(os.environ.get('ZS_REVIEW_BROWSER_ARTIFACT_ROOT', settings.data_dir / 'review-browser'))
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    receipt = root / f'{variant}.json'
    receipt.write_text(json.dumps({'surface': surface, 'decision': decision, 'width': width,
                                  'closed': closed, 'expanded': expanded, 'overflow': overflow}, indent=2))
    receipt.chmod(0o600)
    screenshot = root / f'{variant}.png'
    page.screenshot(path=str(screenshot), full_page=True)
    screenshot.chmod(0o600)


@pytest.mark.parametrize('surface', ['full', 'compact'])
@pytest.mark.parametrize('width', [390, 1440])
def test_reading_plan_is_visible_once_and_readable_before_opening_original(application_browser, surface, width):
    from patchright.sync_api import expect

    page, client, settings, seen = application_browser
    variant = f'plan-{surface}-{width}'
    key = _seed(settings, variant)
    entry = _review_cache._read_all()[key]
    targets = ['§4.2 Held-out evaluation — compare site splits', 'Figure 3 — inspect failure cases']
    skipped = 'Appendix A — notation recap'
    extra = 'The site-split protocol and individual failure examples.'
    entry['digest'].update(read_decision='read', basis='full_text', novelty=5, significance=4,
                           writing_friction='low', read_parts=targets, skip_parts=[skipped],
                           original_value=extra, estimated_read_minutes=12)
    entry['quality'].update(quality_band='neutral', red_flags=[])
    entry['goal_summaries'][0]['abstained'] = False
    _publish_current(key, entry)
    payload = client.get('/api/golden/review-detail', params={'item_key': key}).json()['deep_review']
    assert payload['digest']['read_decision'] == 'skim'
    assert payload['model_read_decision'] == 'read' and 'strong_evidence_not_confirmed' in payload['reading_policy_flags']
    page.set_viewport_size({'width': width, 'height': 900})
    page.goto('http://127.0.0.1/')
    page.get_by_role('button', name='Skip for now', exact=True).click()
    if surface == 'compact':
        page.get_by_role('button').filter(has_text=f'Synthetic reading {variant}').click()
    else:
        page.goto(f'http://127.0.0.1/paper/{key}')
    plan = page.get_by_role('region', name='Reading plan')
    expect(plan).to_be_visible()
    assert not plan.evaluate("e => !!e.closest('details')")
    expect(page.get_by_text('12 min', exact=True)).to_be_visible()
    for text in [*targets, skipped, extra]:
        expect(page.get_by_text(text, exact=True)).to_have_count(1)
        expect(plan.get_by_text(text, exact=True)).to_be_visible()
    assert plan.locator('ol li').all_text_contents() == targets
    colors = page.evaluate(CONTRAST, '[aria-label="Reading plan"]')
    overflow = _no_overflow(page)
    root = Path(os.environ.get('ZS_REVIEW_BROWSER_ARTIFACT_ROOT', settings.data_dir / 'review-browser'))
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    (root / f'{variant}.json').write_text(json.dumps({'colors': colors, 'overflow': overflow,
                                                    'requests': seen}, indent=2))
    page.screenshot(path=str(root / f'{variant}.png'), full_page=True)
    assert overflow['scroll'] <= overflow['client'], overflow
    assert colors and all(row['ratio'] >= 4.5 for row in colors), colors
    assert not any(method != 'GET' for method, _path, _status in seen), seen


@pytest.mark.parametrize('surface', ['full', 'compact'])
@pytest.mark.parametrize('width', [390, 1440])
def test_incomplete_saved_targets_withhold_plan_without_rewriting_or_starting_work(application_browser, surface, width):
    from patchright.sync_api import expect

    page, client, settings, seen = application_browser
    variant = f'withheld-{surface}-{width}'
    key = _seed(settings, variant)
    entry = _review_cache._read_all()[key]
    entry['digest'].update(read_decision='read', basis='abstract', read_parts=[],
                           original_value='Retained unsupported original value.', estimated_read_minutes=13)
    _review_cache._write_one(key, entry)
    before = _review_cache._cache_path().read_bytes()
    payload = client.get('/api/golden/review-detail', params={'item_key': key}).json()['deep_review']
    assert payload is None
    assert _review_cache.get_current_review(key) is None
    page.set_viewport_size({'width': width, 'height': 900})
    page.goto('http://127.0.0.1/')
    page.get_by_role('button', name='Skip for now', exact=True).click()
    if surface == 'compact':
        page.get_by_role('button').filter(has_text=f'Synthetic reading {variant}').click()
    else:
        page.goto(f'http://127.0.0.1/paper/{key}')
    expect(page.get_by_role('region', name='Reading plan')).to_have_count(0)
    expect(page.get_by_text('13 min', exact=True)).to_have_count(0)
    expect(page.get_by_text('Retained unsupported original value.', exact=True)).to_have_count(0)
    assert _review_cache._cache_path().read_bytes() == before
    assert not any(method != 'GET' for method, _path, _status in seen), seen
