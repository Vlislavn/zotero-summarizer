"""Built task navigation over the existing isolated ASGI/SQLite browser fixture."""
from datetime import datetime, timezone
import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from tests.test_application_browser_live import application_browser  # noqa: F401
from tests.test_connection_browser import connection_build  # noqa: F401 — explicit disposable build
from tests.test_research_feed import _seed
from zotero_summarizer.services.research_feed.runner import run_weekly
from zotero_summarizer.storage.feed_identity import stable_feed_key_from_parts


@pytest.mark.parametrize('width', [320, 390, 1440])
def test_built_task_navigation_scope_return_and_no_implicit_work(application_browser, width, monkeypatch):
    from patchright.sync_api import expect
    from zotero_summarizer.integrations import app_rss

    def owned_dns(host, port, **_options):
        assert host == 'example.invalid', 'Unplanned DNS in an isolated navigation journey'
        return [(None, None, None, None, ('8.8.8.8', port))]

    def no_source_http(*_args, **_kwargs):
        raise AssertionError('Source fetching was not authorized by this navigation journey')

    monkeypatch.setattr(app_rss.socket, 'getaddrinfo', owned_dns)
    monkeypatch.setattr(app_rss, 'stream_public_url', no_source_http)
    page, client, settings, seen = application_browser
    page.set_viewport_size({'width': width, 'height': 1000})
    before = client.get('/api/config').json()
    assert before['llm_enabled'] is False
    for name, enabled in [('Synthetic source with a long readable name', True), ('Another saved source', False)]:
        response = client.post('/api/rss/feeds', json={'name': name, 'url': f'https://example.invalid/{enabled}/rss', 'enabled': enabled})
        assert response.status_code == 200, response.text
    config_writes = []
    page.on('request', lambda request: config_writes.append(request.post_data_json)
            if request.method == 'PUT' and urlsplit(request.url).path == '/api/config' else None)

    def picture(name):
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        action = {'selection-draft': 'Save article selection', 'rss-editor': 'Save subscription',
                  'calibration': 'Start review calibration'}.get(name)
        button = page.get_by_role('button', name=action, exact=True) if action else None
        state = {'scene': name, 'width': width}
        if button:
            expect(button).to_be_enabled()
            state['enabled'] = not button.is_disabled()
            state['before'] = button.evaluate('(node) => ({color: getComputedStyle(node).color, background: getComputedStyle(node).backgroundColor})')
        page.screenshot(path=str(settings.data_dir / f'navigation-{name}-{width}.png'), full_page=True, animations='disabled')
        if button:
            state['after'] = button.evaluate('(node) => ({color: getComputedStyle(node).color, background: getComputedStyle(node).backgroundColor})')
        (settings.data_dir / f'navigation-{name}-{width}-state.json').write_text(json.dumps(state, indent=2))

    page.goto('http://127.0.0.1/settings?keep=nav')
    expect(page.get_by_role('heading', name='Settings', exact=True)).to_be_visible()
    expect(page.get_by_role('textbox')).to_have_count(0)
    expect(page.get_by_role('button', name='Save changes', exact=True)).to_have_count(0)
    expect(page.get_by_role('button', name='Verify setup', exact=True)).to_have_count(0)
    picture('overview')
    page.get_by_role('link', name='Change output language', exact=True).click()
    page.get_by_label('Output language', exact=True).fill('German')
    page.get_by_role('button', name='Save output language', exact=True).click()
    expect(page.get_by_text('Output language saved.', exact=True)).to_be_visible()
    assert len(config_writes) == 1
    assert {key for key in before if config_writes[0].get(key) != before[key]} == {'output_language'}
    assert config_writes[0]['_revision'] == before['_revision']
    page.get_by_role('link', name='Back to Settings', exact=True).click()
    expect(page.get_by_role('link', name='Change output language', exact=True)).to_be_focused()
    expect(page).to_have_url('http://127.0.0.1/settings?keep=nav')
    page.get_by_role('link', name='Change AI mode', exact=True).click()
    expect(page.get_by_role('checkbox', name='Enable AI-assisted features', exact=True)).not_to_be_checked()
    page.get_by_role('link', name='Back to Settings', exact=True).click()
    assert len(config_writes) == 1

    page.goto('http://127.0.0.1/today?keep=nav')
    page.get_by_role('link', name='Article selection', exact=True).click()
    selection_before = client.get('/api/config').json()
    goals = page.get_by_label('Goals (one per line)', exact=True)
    goals.fill('Synthetic updated article goal')
    page.get_by_role('button', name='Save article selection', exact=True).click()
    expect(page.get_by_text('Article selection saved.', exact=True)).to_be_visible()
    assert len(config_writes) == 2
    assert {key for key in selection_before if config_writes[1].get(key) != selection_before[key]} == {'research_goals'}
    goals.fill('Retained after cancelled leave')
    page.once('dialog', lambda dialog: dialog.dismiss())
    page.get_by_role('link', name='Back to Today', exact=True).click()
    expect(goals).to_have_value('Retained after cancelled leave')
    assert client.get('/api/config').json()['research_goals'] == ['Synthetic updated article goal']
    if width in (390, 1440):
        picture('selection-draft')
    page.get_by_role('button', name='Discard draft', exact=True).click()
    page.get_by_role('link', name='Back to Today', exact=True).click()
    expect(page.get_by_role('link', name='Article selection', exact=True)).to_be_focused()
    expect(page).to_have_url('http://127.0.0.1/today?keep=nav')
    page.get_by_role('link', name='RSS sources', exact=True).click()
    expect(page.get_by_role('button', name='Edit Synthetic source with a long readable name', exact=True)).to_be_visible()
    expect(page.get_by_role('textbox', name='Name', exact=True)).to_have_count(0)
    expect(page.get_by_role('button', name='Add Telegram source', exact=True)).to_have_count(0)
    page.get_by_role('button', name='Edit Synthetic source with a long readable name', exact=True).click()
    page.get_by_label('Name', exact=True).fill('Edited RSS source')
    if width in (320, 1440):
        picture('rss-editor')
    page.get_by_role('button', name='Save subscription', exact=True).click()
    expect(page.get_by_role('textbox', name='Name', exact=True)).to_have_count(0)
    edited = page.get_by_role('button', name='Edit Edited RSS source', exact=True)
    expect(edited).to_be_visible()
    expect(edited).to_be_focused()
    rows = client.get('/api/rss/feeds').json()['feeds']
    assert any(row['name'] == 'Edited RSS source' and row['enabled'] for row in rows)
    assert any(row['name'] == 'Another saved source' and not row['enabled'] for row in rows)
    assert page.get_by_role('link', name='Today → Triage backlog', exact=True).get_attribute('href') == '/today?keep=nav'

    key = stable_feed_key_from_parts(guid='navigation-browser-paper')
    _seed(settings, key, 'Synthetic navigation paper')
    result = run_weekly(settings, start=datetime(2026, 8, 20, tzinfo=timezone.utc),
                        end=datetime(2026, 8, 29, tzinfo=timezone.utc), review_loader=lambda _key: None)
    run_id = Path(result['json_path']).parent.name
    page.goto(f'http://127.0.0.1/weekly?run={run_id}&keep=nav')
    expect(page.get_by_role('combobox', name='Saved digest', exact=True)).to_have_value(run_id)
    page.get_by_role('link', name='Telegram sources', exact=True).click()
    expect(page.get_by_role('heading', name='Telegram sources', exact=True)).to_be_visible()
    expect(page.get_by_text('Telegram posts are selected by your research goals', exact=False)).to_be_visible()
    expect(page.get_by_role('button', name='Collect & review selected sources', exact=True)).to_be_disabled()
    if width in (390, 1440):
        picture('telegram')
    page.get_by_role('link', name='Back to Weekly', exact=True).click()
    expect(page.get_by_role('combobox', name='Saved digest', exact=True)).to_have_value(run_id)
    expect(page.get_by_role('link', name='Telegram sources', exact=True)).to_be_focused()
    assert urlsplit(page.url).query == f'run={run_id}&keep=nav'

    page.goto('http://127.0.0.1/ops?tab=system&keep=nav')
    expect(page.get_by_role('heading', name='System', exact=True)).to_be_visible()
    if width == 1440:
        picture('system')
    page.get_by_role('link', name='Check readiness', exact=True).click()
    expect(page.get_by_role('heading', name='Check readiness', exact=True)).to_be_visible()
    expect(page.get_by_role('button', name='Verify setup', exact=True)).to_be_visible()
    if width == 390:
        picture('readiness')
    page.get_by_role('link', name='Back to System', exact=True).click()
    expect(page.get_by_role('link', name='Check readiness', exact=True)).to_be_focused()
    page.get_by_role('link', name='Calibrate reviews', exact=True).click()
    expect(page.get_by_role('button', name='Start review calibration', exact=True)).to_be_visible()
    if width == 320:
        picture('calibration')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    writes = [(method, path) for method, path, _status in seen if method in {'POST', 'PUT', 'PATCH', 'DELETE'}]
    assert all(path == '/api/config' or path.startswith('/api/rss/feeds/') for _method, path in writes), writes
    assert client.get('/api/config').json()['llm_enabled'] is False
    print('NAVIGATION_BROWSER_RECEIPT', json.dumps({'width': width, 'requests': seen, 'images': str(settings.data_dir)}))


def test_built_foreign_connection_read_cannot_lend_setup_a_revision(application_browser, monkeypatch):
    from patchright.sync_api import expect
    from zotero_summarizer.integrations import llm_transport

    transports = []

    def no_provider_http(*_args, **_kwargs):
        transports.append('unexpected')
        raise AssertionError('No provider transport is authorized by this concurrency control')

    monkeypatch.setattr(llm_transport, 'owned_http_client', no_provider_http)
    page, client, settings, seen = application_browser
    page.set_viewport_size({'width': 390, 'height': 1000})
    initial = client.get('/api/config').json()
    provider = next(row for row in initial['llm_routing']['providers']
                    if row['name'] == initial['llm_routing']['default']['provider'])
    initial.update(llm_enabled=True, research_goals=['Original research'])
    seeded = client.put('/api/config', json=initial)
    assert seeded.status_code == 200, seeded.text
    base = client.get('/api/config').json()
    page.route('**/api/setup/ai-presets', lambda route: route.fulfill(status=200, json={
        'presets': [], 'local_profiles': {'hardware': {}, 'profiles': [
            {'id': 'existing', 'label': 'Synthetic existing profile', 'model': initial['llm_routing']['default']['model'],
             'compatible': True, 'provider': provider}]}}))
    writes, revisions = [], {}
    page.on('request', lambda request: writes.append(request.post_data_json)
            if request.method == 'PUT' and urlsplit(request.url).path == '/api/config' else None)

    def register_then_concurrent_write(route):
        if route.request.method != 'POST':
            route.fallback()
            return
        own = client.post('/api/llm/connections', json=route.request.post_data_json)
        assert own.status_code == 200, own.text
        revisions['own'] = own.json()['revision']
        foreign = client.get('/api/config').json()
        foreign['research_goals'] = ['Concurrent research']
        saved = client.put('/api/config', json=foreign)
        assert saved.status_code == 200, saved.text
        revisions['foreign'] = client.get('/api/config').json()['_revision']
        route.fulfill(status=200, content_type='application/json', body=own.text)

    page.route('**/api/llm/connections', register_then_concurrent_write)
    page.goto('http://127.0.0.1/setup')
    page.get_by_role('radio', name='Full local', exact=False).check()
    page.get_by_role('button', name='Next', exact=True).click()
    expect(page.get_by_role('heading', name='Zotero sync', exact=True)).to_be_visible()
    page.get_by_role('button', name='Next', exact=True).click()
    expect(page.get_by_role('heading', name='Connect AI', exact=True)).to_be_visible()
    page.get_by_role('button', name='Next', exact=True).click()
    page.get_by_label('Research goals (one per line)', exact=True).fill('My retained draft')
    page.get_by_role('button', name='Back', exact=True).click()
    page.get_by_role('button', name='Add provider', exact=True).click()
    page.get_by_label('API address', exact=True).fill('http://127.0.0.1:9911/exact')
    with page.expect_response(lambda response: urlsplit(response.url).path == '/api/llm/connections'
                              and response.request.method == 'POST'):
        page.get_by_role('button', name='Connect provider', exact=True).click()
    expect(page.get_by_role('button', name='Test saved model', exact=True)).to_be_enabled()
    page.get_by_role('button', name='Next', exact=True).click()
    expect(page.get_by_label('Research goals (one per line)', exact=True)).to_have_value('My retained draft')
    with page.expect_response(lambda response: urlsplit(response.url).path == '/api/config'
                              and response.request.method == 'PUT') as finish:
        page.get_by_role('button', name='Finish', exact=True).click()
    actual = client.get('/api/config').json()
    receipt = {'base_revision': base['_revision'], 'connection_outcome_revision': revisions['own'],
               'foreign_revision': revisions['foreign'], 'finish_status': finish.value.status,
               'submitted_revision': writes[0]['_revision'], 'saved_research_after_finish': actual['research_goals'],
               'browser_config_writes': len(writes), 'profile_compatibility': 'synthetic UI fixture, not hardware proof'}
    (settings.data_dir / 'navigation-foreign-publication-receipt.json').write_text(json.dumps(receipt, indent=2))
    print('NAVIGATION_FOREIGN_PUBLICATION_RECEIPT', json.dumps(receipt))
    assert finish.value.status == 409, receipt
    assert len(writes) == 1 and writes[0]['_revision'] == base['_revision'], receipt
    assert actual['research_goals'] == ['Concurrent research'], receipt
    assert revisions['own'] != revisions['foreign'] and transports == []
    assert not [row for row in seen if row[0] == 'POST' and row[1] in {
        '/api/setup/doctor', '/api/setup/calibrate', '/api/admin/llm-check', '/api/library/university-login'}]
    expect(page.get_by_role('heading', name='Setup saved', exact=True)).to_have_count(0)
    page.screenshot(path=str(settings.data_dir / 'navigation-foreign-publication-390.png'),
                    full_page=True, animations='disabled')


@pytest.mark.parametrize('owned_changed', [False, True], ids=['same-owned', 'changed-owned'])
def test_built_saved_value_read_retains_new_typing(application_browser, owned_changed):
    from patchright.sync_api import expect

    page, client, settings, seen = application_browser
    page.set_viewport_size({'width': 390, 'height': 1000})
    original = client.get('/api/config').json()
    held_write, held_read, browser_writes = [], [], []
    page.on('request', lambda request: browser_writes.append(request.post_data_json)
            if request.method == 'PUT' and urlsplit(request.url).path == '/api/config' else None)

    def hold_submission(route):
        if route.request.method != 'PUT':
            route.fallback()
            return
        reply = client.put('/api/config', json=route.request.post_data_json)
        assert reply.status_code == 200, reply.text
        held_write.append((route, reply.json()))

    page.route('**/api/config', hold_submission)
    page.goto('http://127.0.0.1/today?task=selection')
    page.get_by_label('Goals (one per line)', exact=True).fill('Submitted before leaving')
    page.get_by_role('button', name='Save article selection', exact=True).click()
    expect(page.get_by_role('button', name='Saving…', exact=True)).to_be_disabled()
    assert len(held_write) == 1
    page.once('dialog', lambda dialog: dialog.accept())
    page.get_by_role('link', name='Settings', exact=True).click()
    page.get_by_role('link', name='Change output language', exact=True).click()
    field = page.get_by_label('Output language', exact=True)
    expect(field).to_have_value('English')
    if owned_changed:
        concurrent = client.get('/api/config').json()
        concurrent['output_language'] = 'French'
        reply = client.put('/api/config', json=concurrent)
        assert reply.status_code == 200, reply.text
    held_write[0][0].fulfill(status=200, json=held_write[0][1])
    expect(page.get_by_text('Saved settings changed elsewhere.', exact=False)).to_be_visible()

    def hold_read(route):
        assert route.request.method == 'GET', 'The language readback cannot submit another config write'
        reply = client.get('/api/config')
        assert reply.status_code == 200, reply.text
        held_read.append((route, reply.json()))

    page.route('**/api/config', hold_read)
    page.get_by_role('button', name='Read saved values; keep draft', exact=True).click()
    expect(page.get_by_role('button', name='Read saved values; keep draft', exact=True)).to_be_disabled()
    expect(field).to_be_enabled()
    field.fill('Typed while read was pending')
    assert len(held_read) == 1
    saved = held_read[0][1]
    held_read[0][0].fulfill(status=200, json=saved)
    expect(field).to_have_value('Typed while read was pending')
    if owned_changed:
        expect(page.get_by_role('region', name='Review changed saved values', exact=True)).to_be_visible()
        expect(page.get_by_text('Saved now: French', exact=True)).to_be_visible()
    else:
        expect(page.get_by_role('button', name='Save output language', exact=True)).to_be_enabled()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.screenshot(path=str(settings.data_dir / f'navigation-readback-{owned_changed}-390.png'),
                    full_page=True, animations='disabled')
    assert len(browser_writes) == 1 and browser_writes[0]['research_goals'] == ['Submitted before leaving']
    assert browser_writes[0]['_revision'] == original['_revision']
    assert not [(method, path) for method, path, _status in seen if method in {'POST', 'PUT', 'PATCH', 'DELETE'}]
    if owned_changed:
        page.get_by_role('button', name='Use saved values instead', exact=True).click()
        expect(field).to_have_value('French')
        expect(page.get_by_role('button', name='Save output language', exact=True)).to_be_disabled()
    assert client.get('/api/config').json()['output_language'] == saved['output_language']
    print('NAVIGATION_READBACK_RECEIPT', json.dumps({'owned_changed': owned_changed,
          'original_revision': original['_revision'], 'saved_revision': saved['_revision'],
          'prior_write': 'actual selection PUT committed; its ACK delivered after explicit pending leave',
          'external_write': 'one actual isolated language PUT only in changed-owned control',
          'held_get': 'actual saved GET delivered through owned browser route; no forced query-cache hook',
          'browser_writes': len(browser_writes), 'requests': seen}))
