"""Built Settings → real API/config → owned metadata transport, never a model/server."""
import os
from pathlib import Path

import httpx
import pytest

from tests.test_application_browser_live import application_browser

pytestmark = pytest.mark.skipif(os.environ.get('ZS_APPLICATION_BROWSER_SMOKE') != '1', reason='opt-in built browser')


@pytest.fixture(autouse=True)
def connection_build(monkeypatch):
    from zotero_summarizer.api import app
    build = os.environ.get('CONNECTION_BROWSER_DIST')
    if not build:
        pytest.skip('explicit disposable CONNECTION_BROWSER_DIST required')
    path = Path(build).resolve()
    assert (path / 'index.html').is_file()
    monkeypatch.setattr(app, '_FRONTEND_DIST', path)


def test_built_connection_register_impact_apply_reload_and_dirty_preservation(application_browser, monkeypatch):
    from patchright.sync_api import expect
    from zotero_summarizer.integrations import llm_transport, llm_models

    page, client, settings, seen = application_browser
    config = client.get('/api/config').json()
    config.update(llm_enabled=True, research_goals=['Synthetic saved research'])
    config['llm_routing'] = {
        'providers': [
            {'name': 'old', 'type': 'openai', 'base_url': 'http://127.0.0.1:9999/old', 'api_key_env': 'UNUSED', 'auth_mode': 'none'},
            {'name': 'other', 'type': 'openai', 'base_url': 'http://127.0.0.1:9998/other', 'api_key_env': 'UNUSED', 'auth_mode': 'none'},
        ],
        'default': {'provider': 'old', 'model': 'old-model'},
        'feed': {'model': 'feed-exception'}, 'backlog': {'provider': 'other'},
        'deep_review': {'provider': 'old', 'model': 'review-exception'},
    }
    assert client.put('/api/config', json=config).status_code == 200
    # Existing background reachability is metadata-only too; no real endpoint exists.
    original_catalog = llm_models.list_openai_models
    monkeypatch.setattr(llm_models, 'list_openai_models', lambda *a, **kw: ['served-model'])
    page.goto('http://127.0.0.1/today?task=selection&keep=browser')
    page.get_by_label('Goals (one per line)').fill('Unsaved research draft')
    page.once('dialog', lambda dialog: dialog.dismiss())
    page.get_by_role('link', name='Back to Today', exact=True).click()
    expect(page.get_by_label('Goals (one per line)')).to_have_value('Unsaved research draft')
    page.once('dialog', lambda dialog: dialog.accept())
    page.get_by_role('link', name='Back to Today', exact=True).click()
    expect(page).to_have_url('http://127.0.0.1/today?keep=browser')
    page.goto('http://127.0.0.1/settings?task=university')
    page.get_by_label('Library login URL').fill('https://synthetic-university.invalid/login')
    page.once('dialog', lambda dialog: dialog.dismiss())
    page.get_by_role('link', name='Back to Settings', exact=True).click()
    expect(page.get_by_label('Library login URL')).to_have_value('https://synthetic-university.invalid/login')
    page.once('dialog', lambda dialog: dialog.accept())
    page.get_by_role('link', name='Back to Settings', exact=True).click()
    expect(page.get_by_role('heading', name='Settings', exact=True)).to_be_visible()
    page.goto('http://127.0.0.1/settings?task=connection')
    expect(page.get_by_role('button', name='Change Fallback (steps without their own model)', exact=True)).to_be_visible()
    expect(page.get_by_label('API address', exact=True)).to_have_count(0)
    page.get_by_role('button', name='Add provider', exact=True).click()
    page.get_by_label('API address', exact=True).fill('http://[::1]:9911/exact/proxy')
    expect(page.locator('#ai-models input[type=password]')).to_have_count(0)
    expect(page.get_by_label('API protocol', exact=True)).not_to_be_visible()
    assert 'http://[::1]:9911/exact/proxy' in page.get_by_text('Save an unused connection and fetch model names', exact=False).inner_text()
    page.locator('#ai-models').screenshot(animations='disabled', path=str(settings.data_dir / 'connection-address-390.png'))
    wire = []
    original_http = llm_transport.owned_http_client
    def metadata(request):
        wire.append((request.method, str(request.url), dict(request.headers)))
        return httpx.Response(200, json={'data': [{'id': 'served-model'}]})
    # Restore the real catalog leaf; every later HTTP send uses the owned mock.
    monkeypatch.setattr(llm_models, 'list_openai_models', original_catalog)
    monkeypatch.setattr(llm_transport, 'owned_http_client', lambda *a, **kw: original_http(*a, **kw, transport=httpx.MockTransport(metadata)))
    page.get_by_role('button', name='Connect provider', exact=True).click()
    expect(page.get_by_label('Model', exact=True)).to_be_visible()
    registered = client.get('/api/config').json()
    assert registered['llm_routing']['default'] == config['llm_routing']['default']
    page.get_by_label('Model', exact=True).select_option('served-model')
    expect(page.get_by_role('region', name='Connection impact')).to_be_visible()
    expect(page.get_by_role('button', name='Use as default', exact=True)).to_have_count(0)
    expect(page.get_by_role('option', name='Deliberately keep the unverified inherited combination')).to_have_count(0)
    page.get_by_label('New paper screening', exact=True).select_option('previous')
    page.get_by_label('AI backlog', exact=True).select_option('default')
    expect(page.get_by_role('button', name='Use as default', exact=True)).to_be_visible()
    expect(page.get_by_role('button', name='Save other settings', exact=True)).to_have_count(0)
    expect(page.get_by_role('button', name='Save changes', exact=True)).to_have_count(0)
    expect(page.get_by_label('Goals (one per line)')).to_have_count(0)
    expect(page.get_by_label('Library login URL')).to_have_count(0)
    page.set_viewport_size({'width': 390, 'height': 844})
    page.locator('#ai-models').screenshot(animations='disabled', path=str(settings.data_dir / 'connection-impact-390.png'))
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.set_viewport_size({'width': 1440, 'height': 1000})
    page.locator('#ai-models').screenshot(animations='disabled', path=str(settings.data_dir / 'connection-impact-1440.png'))
    page.get_by_role('button', name='Use as default', exact=True).click()
    expect(page.get_by_text('Selection saved.', exact=False)).to_be_visible()
    expect(page.get_by_label('Model', exact=True)).to_have_count(0)
    expect(page.get_by_label('Goals (one per line)')).to_have_count(0)
    expect(page.get_by_label('Library login URL')).to_have_count(0)
    saved = client.get('/api/config').json()
    assert saved['research_goals'] == ['Synthetic saved research']
    assert saved['university_access']['login_url'] != 'https://synthetic-university.invalid/login'
    assert saved['llm_routing']['default']['model'] == 'served-model'
    assert saved['llm_routing']['feed'] == {'provider': 'old', 'model': 'feed-exception'}
    assert saved['llm_routing']['backlog'] == {'provider': None, 'model': None}
    assert saved['llm_routing']['deep_review'] == registered['llm_routing']['deep_review']
    page.get_by_role('button', name='Test saved model', exact=True).click()
    expect(page.get_by_text('Test not sent', exact=True)).to_be_visible()
    expect(page.get_by_text('Reason:', exact=False)).not_to_be_visible()
    page.set_viewport_size({'width': 390, 'height': 844})
    page.locator('#ai-models').screenshot(animations='disabled', path=str(settings.data_dir / 'connection-test-unknown-390.png'))
    page.reload()
    expect(page.get_by_text('served-model', exact=True)).to_have_count(2)
    page.set_viewport_size({'width': 320, 'height': 900})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.locator('#ai-models').screenshot(animations='disabled', path=str(settings.data_dir / 'connection-saved-320.png'))
    assert wire and all(method == 'GET' and url.endswith('/models') and 'authorization' not in headers for method, url, headers in wire)
    forbidden = ('/api/admin/llm-check', '/api/setup/doctor', '/api/setup/calibrate', '/api/research-feed/telegram/runs', '/api/library/university-login')
    assert not [row for row in seen if row[0] == 'POST' and row[1] in forbidden]
    print('CONNECTION_SCREENSHOTS', settings.data_dir)


def test_built_native_bootstrap_new_authorization_catalog_refusal_and_explicit_test(application_browser, monkeypatch):
    from patchright.sync_api import expect
    from zotero_summarizer.services._common import state
    from zotero_summarizer.services.llm import credentials
    from zotero_summarizer.integrations import llm_transport, llm_requests

    page, client, settings, _seen = application_browser
    settings.config_path.unlink()
    state().app_state = None
    state().config_disk_revision = None
    memory, wire = {}, []
    monkeypatch.setattr(credentials.keyring, 'get_password', lambda _service, slot: memory.get(slot))
    monkeypatch.setattr(credentials.keyring, 'set_password', lambda _service, slot, secret: memory.update({slot: secret}))
    result = {'status': 200}
    def reply(request):
        wire.append(request)
        if request.method == 'GET' or result['status'] != 200:
            return httpx.Response(401, json={'error': 'synthetic-sensitive-provider-body'})
        return httpx.Response(200, json={'id':'msg_browser','type':'message','role':'assistant','model':'synthetic',
            'content':[{'type':'text','text':'Synthetic browser response'}], 'stop_reason':'end_turn',
            'usage':{'input_tokens':1,'output_tokens':1}})
    owned = llm_transport.owned_http_client
    verify = llm_requests.verification_http_client
    monkeypatch.setattr(llm_transport, 'owned_http_client', lambda *a, **kw: owned(*a, **kw, transport=httpx.MockTransport(reply)))
    monkeypatch.setattr(llm_requests, 'verification_http_client', lambda hook: verify(hook, transport=httpx.MockTransport(reply)))
    page.goto('http://127.0.0.1/settings#ai-models')
    expect(page.get_by_label('API address', exact=True)).to_be_visible()
    page.get_by_text('Server address, API type and access', exact=True).click()
    page.get_by_label('API protocol', exact=True).select_option('anthropic')
    page.get_by_label('API address', exact=True).fill('')
    page.get_by_label('Authorization', exact=True).select_option('new')
    page.get_by_role('button', name='Connect provider', exact=True).click()
    expect(page.get_by_text('Add an API key for this connection', exact=True)).to_be_visible()
    page.get_by_text('Add an API key for this connection', exact=True).click()
    page.get_by_role('button', name='Review authorization impact', exact=True).click()
    page.get_by_label('API key', exact=True).fill('synthetic-browser-secret')
    page.get_by_role('button', name='Store API key', exact=True).click()
    expect(page.get_by_text('Authorization stored separately.', exact=False)).to_be_visible()
    assert not settings.config_path.exists() and wire == []
    page.get_by_label('Model', exact=True).fill('synthetic')
    expect(page.get_by_role('button', name='Use as default', exact=True)).to_be_visible()
    page.get_by_role('group', name='Connection and model', exact=True).screenshot(animations='disabled', path=str(settings.data_dir / 'connection-native-bootstrap-auth-390.png'))
    page.get_by_role('button', name='Use as default', exact=True).click()
    expect(page.get_by_text('synthetic', exact=True)).to_have_count(4)
    assert client.get('/api/config').json()['llm_routing']['providers'][0]['base_url'] is None
    page.get_by_role('button', name='Test saved model', exact=True).click()
    expect(page.get_by_text('Synthetic browser response', exact=True)).to_be_visible()
    page.locator('#ai-models').screenshot(animations='disabled', path=str(settings.data_dir / 'connection-native-test-response-390.png'))
    result['status'] = 401
    page.get_by_role('button', name='Test saved model', exact=True).click()
    expect(page.get_by_text('Server returned HTTP 401.', exact=False)).to_be_visible()
    assert 'synthetic-sensitive-provider-body' not in page.locator('body').inner_text()
    page.locator('#ai-models').screenshot(animations='disabled', path=str(settings.data_dir / 'connection-native-test-http401-390.png'))
    page.get_by_role('button', name='Change Fallback (steps without their own model)', exact=True).click()
    page.get_by_role('button', name='Load models', exact=True).click()
    expect(page.get_by_text('Server returned HTTP 401.', exact=False)).to_be_visible()
    page.locator('#ai-models').screenshot(animations='disabled', path=str(settings.data_dir / 'connection-native-catalog-http401-390.png'))
    assert len(client.get('/api/config').json()['llm_routing']['providers']) == 1
    assert len([request for request in wire if request.method == 'POST']) == 2
    assert all(request.url.host == 'api.anthropic.com' and request.headers['x-api-key'] == 'synthetic-browser-secret' for request in wire)


def test_built_lost_registration_ack_reads_effect_without_replay(application_browser, monkeypatch):
    from patchright.sync_api import expect
    from zotero_summarizer.integrations import llm_models

    page, client, settings, seen = application_browser
    config = client.get('/api/config').json()
    config['llm_enabled'] = True
    assert client.put('/api/config', json=config).status_code == 200
    monkeypatch.setattr(llm_models, 'list_openai_models', lambda *a, **kw: ['synthetic'])
    before = len(client.get('/api/config').json()['llm_routing']['providers'])
    writes = []
    def lose_ack(route):
        if route.request.method != 'POST':
            route.fallback()
            return
        response = client.post('/api/llm/connections', content=route.request.post_data_buffer,
                               headers=route.request.headers)
        assert response.status_code == 200
        writes.append(response.json()['operation_id'])
        route.fulfill(status=200, content_type='application/json', body='{}')
    page.route('**/api/llm/connections', lose_ack)
    page.goto('http://127.0.0.1/settings?task=connection')
    expect(page.get_by_role('region', name='Model for each step', exact=True)).to_be_visible()
    page.get_by_role('button', name='Add provider', exact=True).click()
    page.get_by_label('API address', exact=True).fill('http://[::1]:9922/ack')
    page.get_by_role('button', name='Connect provider', exact=True).click()
    expect(page.get_by_text('Unconfirmed connection response.', exact=False)).to_be_visible()
    page.get_by_role('button', name='Read back intended connection', exact=True).click()
    expect(page.get_by_text('Readback: exact server registered; selection not confirmed.', exact=True)).to_be_visible()
    assert len(writes) == 1 and len(client.get('/api/config').json()['llm_routing']['providers']) == before + 1
    page.locator('#ai-models').screenshot(animations='disabled', path=str(settings.data_dir / 'connection-lost-ack-readback-390.png'))


def test_built_saved_first_scoped_tasks_and_exact_probe_coverage(application_browser, monkeypatch):
    import sys
    from patchright.sync_api import expect
    from zotero_summarizer.integrations import llm_transport, llm_requests
    from zotero_summarizer.services import _adapters

    for name in list(sys.modules):
        if name == 'onprem' or name.startswith('onprem.'):
            monkeypatch.delitem(sys.modules, name)
    from onprem.llm import LLM
    monkeypatch.setattr(_adapters, '_load_onprem', lambda: (LLM, None))
    page, client, settings, seen = application_browser
    config = client.get('/api/config').json()
    config['llm_enabled'] = True
    config['llm_routing'] = {
        'providers': [
            {'name': 'local', 'type': 'openai', 'base_url': 'http://127.0.0.1:9911/v1', 'api_key_env': 'UNUSED_LOCAL', 'auth_mode': 'none'},
            {'name': 'hosted-review', 'type': 'openai', 'base_url': 'https://example.invalid/exact', 'api_key_env': 'UNUSED_REVIEW', 'auth_mode': 'none', 'temperature': 0.7},
            {'name': 'hosted-fast', 'type': 'openai', 'base_url': 'https://example.invalid/exact', 'api_key_env': 'UNUSED_FAST', 'auth_mode': 'none', 'temperature': 0.1},
        ],
        'default': {'provider': 'local', 'model': 'local-fallback'},
        'feed': {'provider': 'hosted-fast', 'model': 'screen-model'},
        'backlog': {'provider': 'hosted-fast', 'model': 'backlog-model'},
        'deep_review': {'provider': 'hosted-review', 'model': 'review-model'},
    }
    initial_save = client.put('/api/config', json=config)
    assert initial_save.status_code == 200, initial_save.text
    wire = []
    def reply(request):
        wire.append(request)
        if request.method == 'GET':
            return httpx.Response(200, json={'data': [{'id': 'chosen-task'}]})
        return httpx.Response(200, json={'id': 'chatcmpl_owned', 'object': 'chat.completion', 'created': 1,
            'model': 'chosen-task', 'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': 'Synthetic task response'}, 'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}})
    owned, verification = llm_transport.owned_http_client, llm_requests.verification_http_client
    monkeypatch.setattr(llm_transport, 'owned_http_client', lambda *a, **kw: owned(*a, **kw, transport=httpx.MockTransport(reply)))
    monkeypatch.setattr(llm_requests, 'verification_http_client', lambda hook: verification(hook, transport=httpx.MockTransport(reply)))
    page.goto('http://127.0.0.1/settings?task=connection')
    expect(page.get_by_role('region', name='Model for each step')).to_be_visible()
    expect(page.get_by_text('review-model', exact=True)).to_be_visible()
    expect(page.get_by_text('screen-model', exact=True)).to_be_visible()
    expect(page.get_by_label('API address', exact=True)).to_have_count(0)
    page.locator('#ai-models').screenshot(animations='disabled', path=str(settings.data_dir / 'models-saved-first-390.png'))
    page.get_by_role('button', name='Change Paper reviews & questions', exact=True).click()
    picker = page.get_by_role('combobox', name='Saved provider', exact=True)
    expect(picker).to_have_value('hosted-review')
    assert 'hosted-review · Hosted' in picker.inner_text() and 'hosted-fast · Hosted' in picker.inner_text()
    picker.select_option('hosted-fast')
    page.get_by_role('button', name='Load models', exact=True).click()
    page.get_by_role('combobox', name='Model', exact=True).select_option('chosen-task')
    page.get_by_role('checkbox', name='New paper screening', exact=False).check()
    expect(page.get_by_role('button', name='Use for selected tasks', exact=True)).to_be_enabled()
    page.locator('#ai-models').screenshot(animations='disabled', path=str(settings.data_dir / 'models-task-scope-390.png'))
    page.get_by_role('button', name='Use for selected tasks', exact=True).click()
    expect(page.get_by_text('Selection saved.', exact=False)).to_be_visible()
    saved = client.get('/api/config').json()['llm_routing']
    assert saved['default'] == config['llm_routing']['default'] and saved['backlog'] == config['llm_routing']['backlog']
    assert saved['feed'] == saved['deep_review'] == {'provider': 'hosted-fast', 'model': 'chosen-task'}
    assert saved['providers'][2]['temperature'] == 0.1 and not [r for r in wire if r.method == 'POST']
    page.get_by_role('button', name='Test selected tasks', exact=True).click()
    expect(page.get_by_text('Works — answered', exact=True)).to_have_count(2)
    expect(page.get_by_text('Synthetic task response', exact=True)).to_have_count(2)
    sends = [r for r in wire if r.method == 'POST']
    assert len(sends) == 1 and str(sends[0].url) == 'https://example.invalid/exact/chat/completions'
    assert all('authorization' not in r.headers for r in wire)
    page.locator('#ai-models').screenshot(animations='disabled', path=str(settings.data_dir / 'models-task-tests-390.png'))
    page.get_by_role('button', name='Test saved model', exact=True).click()
    expect(page.get_by_text('Test not sent', exact=True)).to_be_visible()
    assert len([r for r in wire if r.method == 'POST']) == 1
    page.locator('#ai-models').screenshot(animations='disabled', path=str(settings.data_dir / 'models-local-unknown-390.png'))
    page.reload()
    expect(page.get_by_text('chosen-task', exact=True)).to_have_count(2)
    for width in (320, 1440):
        page.set_viewport_size({'width': width, 'height': 1000})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.locator('#ai-models').screenshot(animations='disabled', path=str(settings.data_dir / f'models-saved-first-{width}.png'))
    assert not [r for r in seen if r[0] == 'POST' and r[1] in ('/api/setup/doctor', '/api/admin/llm-check')]
    print('SCOPED_MODEL_SCREENSHOTS', settings.data_dir)
