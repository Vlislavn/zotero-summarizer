"""Opt-in synthetic built-browser receipts; not human/native acceptance.

ZS_REVIEW_BROWSER_ARTIFACT_ROOT explicitly selects a private persistent directory.
Otherwise receipts are transient, beneath the isolated Settings.data_dir.
"""
import json
import os
from pathlib import Path

import pytest

from tests.test_application_browser_live import application_browser  # noqa: F401
from zotero_summarizer.services._common import read_config
from zotero_summarizer.services.library import _review_cache, _review_identity
from zotero_summarizer.storage import feeds
from zotero_summarizer.storage.feed_identity import stable_feed_key_from_parts

pytestmark = pytest.mark.skipif(
    os.environ.get('ZS_APPLICATION_BROWSER_SMOKE') != '1', reason='opt-in real built Chromium',
)


def _seed(settings, variant):
    guid = f'synthetic-reading-{variant}'
    key = stable_feed_key_from_parts(guid=guid)
    with feeds.open_triage_conn(settings.triage_db_path) as conn:
        feeds.record_decision(conn, run_id='synthetic-reading', decision=feeds.DECISION_USER_APPROVED,
                              feed_item={'feed_library_id': 1, 'item_id': 1, 'guid': guid,
                                         'title': f'Synthetic reading {variant}'}, composite_score=4.5)
        conn.commit()
    digest = {'tldr': 'Synthetic contribution.', 'read_decision': 'skim',
              'read_why': 'Synthetic reading rationale.', 'key_findings': ['Synthetic main finding.'],
              'methods': 'Synthetic methods.', 'limitations': 'Synthetic limitations.',
              'key_weakness': 'Synthetic weakness.', 'executive_summary': 'Synthetic reference summary.'}
    quality = {'grade': 'B', 'quality_band': 'neutral', 'coverage_met': 1,
               'coverage_applicable': 2, 'coverage_standard': 'Synthetic standard',
               'red_flags': ['Synthetic caveat.'], 'rubric': {'external_validation': 'no'}}
    goals = [{'goal': 'Synthetic evaluation', 'retrieval_state': 'hit', 'relevant': True,
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
    _review_cache._write_one(key, {'digest': digest, 'quality': quality, 'goal_summaries': goals,
                                 'review_contract_version': _review_cache.REVIEW_CONTRACT_VERSION,
                                 'review_identity': identity})
    return key


CONTRAST = """() => {
 const rgb = s => (s.match(/[\\d.]+/g)||[]).map(Number);
 const blend = (a,b) => a.slice(0,3).map((v,i)=>v*(a[3]??1)+b[i]*(1-(a[3]??1)));
 const lum = a => a.map(v=>v/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4)
   .reduce((s,v,i)=>s+v*[.2126,.7152,.0722][i],0);
 const selector = e => {let parts=[]; while(e && e.tagName!=='MAIN') {
   parts.unshift(e.tagName.toLowerCase()+':nth-child('+(Array.from(e.parentElement.children).indexOf(e)+1)+')');
   e=e.parentElement;} return '.paper-reading-page main > '+parts.join(' > ');};
 return Array.from(document.querySelectorAll('.paper-reading-page main *')).filter(e=>
   e.checkVisibility() && Array.from(e.childNodes).some(n=>n.nodeType===3&&n.textContent.trim())
 ).map(e=>{let chain=[],p=e; while(p){chain.unshift(p);p=p.parentElement;}
   let bg=[255,255,255]; for(const n of chain) bg=blend(rgb(getComputedStyle(n).backgroundColor),bg);
   const s=getComputedStyle(e),fg=blend(rgb(s.color),bg),a=lum(fg),b=lum(bg);
   return {selector:selector(e),text:e.textContent.trim().slice(0,80),color:s.color,
     background:bg,fontSize:s.fontSize,ratio:(Math.max(a,b)+.05)/(Math.min(a,b)+.05)};
 });
}"""


def _no_overflow(page):
    receipt = page.evaluate('''() => ({width:innerWidth, client:document.documentElement.clientWidth,
        scroll:document.documentElement.scrollWidth})''')
    return receipt


def _keyboard_disclosure(page, label):
    from patchright.sync_api import expect

    summary = page.locator('summary').filter(has_text=label)
    expect(summary).to_have_count(1)
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
    _keyboard_disclosure(page, 'evidence')
    expect(page.get_by_text('“Synthetic evidence quote.”', exact=True)).to_be_visible()


@pytest.mark.parametrize('variant', ['full', 'long-incomplete', 'legacy'])
@pytest.mark.parametrize('width', [390, 1024, 1440])
def test_real_built_review_reading(application_browser, variant, width):
    from patchright.sync_api import expect

    page, client, settings, seen = application_browser
    key = _seed(settings, variant)
    response = client.get('/api/golden/review-detail', params={'item_key': key})
    assert response.status_code == 200, response.text
    assert response.json()['deep_review']['quality']['grade'] == 'B'
    page.set_viewport_size({'width': width, 'height': 900})
    page.goto(f'http://127.0.0.1/paper/{key}')
    expect(page.get_by_role('heading', name=f'Synthetic reading {variant}', exact=True)).to_be_visible()
    if variant == 'full':
        _full_contract(page)
    elif variant == 'legacy':
        expect(page.get_by_text('Quality B', exact=True)).to_be_visible()
        expect(page.get_by_text('Synthetic legacy assessment.', exact=True)).to_be_visible()
        expect(page.get_by_text('Older review — re-run for the new digest.', exact=True)).to_be_visible()
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
