"""Owned Chrome -> real Today API/SQLite rendering; synthetic evidence, not usefulness."""
import json
import os

import pytest

from tests._review_question_browser import db_rows, synthetic_lab, write_json
from tests.test_review_reading_browser import CONTRAST, _keyboard_disclosure, _no_overflow
from zotero_summarizer.runtime import get_context
from zotero_summarizer.services.library import _review_cache, _review_identity
from zotero_summarizer.storage import feeds

pytestmark = pytest.mark.skipif(
    os.environ.get('ZS_REVIEW_QUESTION_BROWSER_SMOKE') != '1', reason='opt-in installed owned Chrome',
)


@pytest.fixture
def today_browser(monkeypatch):
    assert os.environ.get('ZS_REVIEW_QUESTION_BROWSER_SYNTHETIC') == '1'
    assert os.environ.get('ZS_REVIEW_QUESTION_BROWSER_SLOT') == 'reserved'
    yield from synthetic_lab(monkeypatch)


def _seed_selection(lab, variant):
    config = get_context().state.app_state.config
    config.quality_review.rank_interleave = variant == 'blinded'
    config.quality_review.rank_quality_first = False
    with feeds.open_triage_conn(lab.database) as conn:
        conn.execute('UPDATE processed_feed_items SET composite_score=?, surprise_score=?, '
                     'quality_review_json=? WHERE stable_feed_key=?',
                     (None if variant == 'unscored' else 3.0, 5.0,
                      json.dumps({'grade': 'A'}) if variant == 'legacy' else None, lab.stable))
        conn.commit()
    if variant in {'current-A', 'current-D', 'needs-pdf', 'not-ready'}:
        pdf = next(lab.settings.pdf_cache_dir.glob('*.pdf'))
        identity = _review_identity.build_review_identity(
            config=config, pdf_path=str(pdf), source_kind='override', focus_prompt='',
        )
        entry = {'review_contract_version': _review_cache.REVIEW_CONTRACT_VERSION,
                 'review_identity': identity, 'quality': {'grade': variant[-1] if variant.startswith('current') else 'B'},
                 'digest': {'tldr': 'Synthetic supplied storage evidence.', 'read_decision': 'skim'}}
        if variant == 'needs-pdf':
            entry.update(needs_pdf=True, digest=None)
        elif variant == 'not-ready':
            entry['digest'] = {}
        _review_cache._write_one(lab.stable, entry)
        assert _review_cache.review_is_current(entry, lab.stable)


EXPECTED = {
    'current-A': 'Evidence: grade A (AI review)',
    'current-D': 'Evidence: grade D (AI review)',
    'legacy': 'Evidence: legacy grade A · current review unconfirmed',
    'unknown': 'Review evidence: unknown',
    'needs-pdf': 'Review: full text unavailable',
    'not-ready': 'Review: not ready',
    'unscored': 'Review evidence: unknown',
    'blinded': 'Review evidence: unavailable (blinded selection)',
}


@pytest.mark.parametrize('variant', list(EXPECTED))
@pytest.mark.parametrize('width', [390, 1440])
def test_real_today_selection_evidence(today_browser, variant, width):
    from patchright.sync_api import expect

    lab = today_browser
    _seed_selection(lab, variant)
    before = db_rows(lab.database, 'SELECT id,decision,composite_score,reading_priority '
                     'FROM processed_feed_items ORDER BY id')
    page = lab.open_browser()
    page.set_viewport_size({'width': width, 'height': 1000})
    page.goto(lab.server.origin + '/today')
    expect(page.get_by_text('Synthetic Today paper', exact=True)).to_be_visible()
    article = page.get_by_role('article')
    expect(article).to_have_count(1)
    expect(article.get_by_text(EXPECTED[variant], exact=True)).to_be_visible()
    relevance = 'Relevance: unscored' if variant == 'unscored' else 'Relevance: moderate'
    expect(article.get_by_text(relevance, exact=True)).to_be_visible()
    _keyboard_disclosure(page, 'Why selected')
    assert article.locator('details').evaluate('(e) => e.open')
    payload = page.evaluate("async () => (await fetch('/api/daily?K=5')).json()")
    assert len(payload['papers']) == 1 and payload['papers'][0]['stable_feed_key'] == lab.stable
    if variant == 'blinded':
        assert payload['selection_policy'] == 'interleave'
        assert payload['papers'][0]['selection_evidence'] == {'mode': 'interleave', 'basis': 'unavailable_blinded'}
        assert article.get_by_text('Cohort key:', exact=True).count() == 0
    else:
        assert payload['selection_policy'] == 'control'
        assert payload['papers'][0]['selection_evidence']['stored']['composite_recorded'] == (variant != 'unscored')
        expect(article.get_by_text('Cohort key:', exact=True)).to_be_visible()
    before_navigation = db_rows(lab.database, 'SELECT id,decision,composite_score,reading_priority '
                                'FROM processed_feed_items ORDER BY id')
    assert before_navigation == before
    contrast = page.evaluate(CONTRAST, 'article')
    # Only the newly exposed evidence, relevance pill and disclosure are this gate's scope.
    relevant = [row for row in contrast if row['text'] in {EXPECTED[variant], relevance, 'Why selected'}
                or row['text'].startswith(('Active policy:', 'Displayed composite:', 'Stored priority (separate):',
                                          'Cohort ranking:', 'Cohort key:', 'Picker:', 'Picker key:', 'Quality adjustment:'))]
    assert len(relevant) >= 3
    overflow = _no_overflow(page)
    receipt = {'variant': variant, 'width': width, 'scope': 'real API/SQLite and supplied synthetic cache, no endpoint substitution',
               'payload': payload, 'contrast': relevant, 'overflow': overflow, 'before': before, 'after': before_navigation}
    stem = f'selection-{variant}-{width}'
    write_json(lab.artifact / f'{stem}.json', receipt)
    lab.checkpoint(stem, page)
    page.get_by_role('link', name='Open full review ↗', exact=True).click()
    expect(page.get_by_role('heading', name='Synthetic Today paper', exact=True)).to_be_visible()
    assert '/paper/' in page.url and not lab.page_errors and not lab.blocked
    assert not [row for row in lab.server.receipts if row['method'] == 'POST'
                and row['path'] in {'/api/daily/add', '/api/daily/trash', '/api/library/ask-paper'}]
    assert overflow['scroll'] <= overflow['client'], overflow
    failures = [row for row in relevant if row['ratio'] < 4.5]
    assert not failures, failures
