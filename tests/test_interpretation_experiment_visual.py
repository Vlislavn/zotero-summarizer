"""Opt-in real Chromium over synthetic qualified HTML; no model/network/native-user writes."""
import hashlib
import json
import os

import pytest

from tests._interpretation_experiment import ScriptedClient
from tests.test_interpretation_experiment_core import run
from tests.test_review_reading_browser import CONTRAST

pytestmark = pytest.mark.skipif(
    os.environ.get('ZS_APPLICATION_BROWSER_SMOKE') != '1', reason='opt-in static interpretation Chromium inspection',
)


@pytest.fixture(scope='module')
def chromium():
    from patchright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            yield browser
        finally:
            browser.close()


@pytest.mark.parametrize('width', [390, 1440])
@pytest.mark.parametrize('state', ['accepted', 'malformed_optional'])
@pytest.mark.parametrize('consumer', ['brief', 'note'])
def test_actual_qualified_interpretation_html_geometry(chromium, tmp_path, width, state, consumer):
    from zotero_summarizer.models import PaperDigest
    from zotero_summarizer.services.library._paper_read_html import write_outputs
    from zotero_summarizer.services.zotero._notes import build_digest_note_html

    record = json.loads(json.dumps(run(ScriptedClient())))
    if state == 'malformed_optional':
        record['interpretation'] = {'status': None, 'draft': 'UNVERIFIED OPTIONAL CONTENT'}
    directory, staging = tmp_path / 'published', tmp_path / 'staging'
    directory.mkdir()
    staging.mkdir()
    if consumer == 'brief':
        result = write_outputs(directory, record['content'], staging=staging,
                               digest=record['digest'], interpretation=record)
        assert result['audit']['status'] == 'passed'
        html = (directory / 'paper_presentation.html').read_text()
    else:
        fragment = build_digest_note_html(PaperDigest.model_validate(record['digest']), interpretation=record)
        html = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width,initial-scale=1"></head>'
                '<body><p>Synthetic HTML inspection; not native Zotero application acceptance.</p>'
                + fragment + '</body></html>')
    artifact = tmp_path / 'data'
    artifact.mkdir()
    name = f'interpretation-{consumer}-{state}-{width}'
    (artifact / (name + '.html')).write_text(html)
    errors, requests = [], []
    context = chromium.new_context(viewport={'width': width, 'height': 900}, service_workers='block')
    try:
        def refuse(route):
            requests.append(route.request.url)
            route.abort()
        context.route('**/*', refuse)
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.set_content(html, wait_until='load')
        page.evaluate('() => Promise.all(document.getAnimations().map(animation => animation.finished))')
        if state == 'accepted':
            assert page.locator('section[data-interpretation="ai-draft"] h3').all_text_contents() == ['Finding', 'Scope', 'Unproven']
        else:
            assert page.locator('section[data-interpretation="ai-draft"]').count() == 0
            assert page.get_by_text('AI draft unavailable: proof_unavailable', exact=True).is_visible()
            assert 'UNVERIFIED OPTIONAL CONTENT' not in page.locator('body').inner_text()
        geometry = page.evaluate('''() => ({viewport: innerWidth, scroll: document.documentElement.scrollWidth,
            sections: [...document.querySelectorAll('section[data-interpretation]')].map(e => {
                const r=e.getBoundingClientRect(); return {left:r.left,right:r.right,width:r.width};})})''')
        assert geometry['scroll'] <= width
        assert all(row['left'] >= 0 and row['right'] <= width for row in geometry['sections'])
        colors = page.evaluate(CONTRAST, 'body')
        assert all(row['ratio'] >= 4.5 for row in colors), colors
        page.screenshot(path=str(artifact / (name + '.png')), full_page=True, animations='disabled')
        if state == 'accepted':
            disclosure = page.locator('details.interpretation-support')
            assert disclosure.count() == 1 and disclosure.get_attribute('open') is None
            disclosure.locator('summary').click()
            assert disclosure.get_attribute('open') is not None
            assert disclosure.get_by_text('Source selection', exact=True).is_visible()
            assert page.evaluate('document.documentElement.scrollWidth') <= width
            colors += page.evaluate(CONTRAST, 'body')
            assert all(row['ratio'] >= 4.5 for row in colors), colors
            page.screenshot(path=str(artifact / (name + '-support.png')), full_page=True, animations='disabled')
        assert requests == [] and errors == []
        (artifact / (name + '.json')).write_text(json.dumps({
            'geometry': geometry, 'sourceHTMLSHA256': hashlib.sha256(html.encode()).hexdigest(),
            'browserVersion': chromium.version, 'networkRequests': requests, 'pageErrors': errors,
            'contrast': colors,
            'syntheticProtocolOnly': True, 'nativeApplicationAcceptance': False,
        }, indent=2) + '\n')
    finally:
        context.close()
