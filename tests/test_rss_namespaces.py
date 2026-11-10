import argparse
import json
import sys
from xml.sax import SAXParseException

import feedparser
import httpx
import pytest

from zotero_summarizer.integrations import app_rss
from zotero_summarizer.storage import feeds, rss


def _body(extra='', *, count=3):
    items = ''.join(
        f'<item><guid>story-{i}</guid><title>Café {i}</title>'
        f'<link>https://example.org/story-{i}</link><description>Body {i}</description></item>'
        for i in range(count)
    )
    return f'<rss version="2.0"><channel><title>Feed</title>{items}{extra}</channel></rss>'


def _reader(tmp_path, monkeypatch, body):
    path = tmp_path / 'rss.sqlite'
    with feeds.open_triage_conn(path) as conn:
        feed_id = rss.upsert_rss_feed(conn, name='Feed', url='https://example.org/feed')
        conn.commit()
    monkeypatch.setattr(app_rss, '_fetch_public_url', lambda *a, **k: (body, httpx.Headers()))
    return app_rss.AppRssReader(path), feed_id


@pytest.mark.parametrize('prefix', ['other', 'ns0', 'дополнение'])
@pytest.mark.parametrize('encoding', ['utf-8', 'utf-16'])
def test_recovers_missing_namespace_without_reparsing_items(tmp_path, monkeypatch, prefix, encoding):
    text = f'<?xml version="1.0" encoding="{encoding}"?>' + _body(f'<{prefix}:extra>Extension</{prefix}:extra>')
    body = text.encode(encoding)
    parsed = feedparser.parse(body)
    assert parsed.bozo and len(parsed.entries) == 3
    reader, feed_id = _reader(tmp_path, monkeypatch, body)
    original_parse = feedparser.parse
    seen = []

    def parse_once(value, *args, **kwargs):
        seen.append(value)
        return original_parse(value, *args, **kwargs)

    monkeypatch.setattr(feedparser, 'parse', parse_once)
    result = reader.refresh_feeds()
    assert result == {'feeds': 1, 'inserted': 3, 'updated': 0, 'incomplete': 0, 'unchanged': 0, 'errors': []}
    assert seen == [body]
    stored = reader.get_feed_items(order='oldest_first', limit=None)
    for row, entry in zip(stored, parsed.entries, strict=True):
        expected = app_rss._entry_to_item(entry, feed={'id': feed_id, 'name': 'Feed'}, feed_title='Feed')
        keys = (expected.keys() & row.keys()) - {'item_id'}
        assert {key: row[key] for key in keys} == {key: expected[key] for key in keys}
        assert row['item_id'] > 0
    issue = reader.get_feed_groups()[0]['last_check_error']
    assert issue.startswith('RssNamespaceWarning:')
    assert not any('urn:zotero-summarizer:rss-unresolved:' in str(row) for row in stored)
    again = reader.refresh_feeds()
    assert again['inserted'] == 0 and again['updated'] == 3 and again['errors'] == []
    monkeypatch.setattr(app_rss, '_fetch_public_url', lambda *a, **k: (_body().encode(), httpx.Headers()))
    reader.refresh_feeds()
    assert reader.get_feed_groups()[0]['last_check_error'] is None


@pytest.mark.parametrize('count', [0])
def test_namespace_warning_with_zero_commits_retains_honest_counts(tmp_path, monkeypatch, count):
    reader, _ = _reader(tmp_path, monkeypatch, _body('<other:extra/>', count=count).encode())
    result = reader.refresh_feeds()
    assert result == {'feeds': 1, 'inserted': 0, 'updated': 0, 'incomplete': 0, 'unchanged': 1, 'errors': []}
    assert reader.get_feed_items() == []
    assert reader.get_feed_groups()[0]['last_check_error'].startswith('RssNamespaceWarning:')


_BAD_EXTRAS = [
    ('mismatch', '<other:extra/></wrong>'),
    ('reserved-binding', '<other:extra/><extra xmlns:xml="urn:wrong"/>'),
    ('invalid-qname', '<other:extra/><a:b:c/>'),
    ('expanded-duplicate', '<other:extra/><dup xmlns:a="urn:same" xmlns:b="urn:same" a:v="1" b:v="2"/>'),
    ('scope-only', '<other:extra/><branch xmlns:p="urn:local"/><p:outside/>'),
    ('late-reserved-binding', '<p:extra/>' * 1100 + '<extra xmlns:xml="urn:wrong"/>' + '<p:extra/>' * 100),
]


@pytest.mark.parametrize('name,extra', _BAD_EXTRAS, ids=[row[0] for row in _BAD_EXTRAS])
def test_namespace_error_cannot_mask_other_xml_errors(tmp_path, monkeypatch, name, extra):
    body = _body(extra).encode()
    parsed = feedparser.parse(body)
    assert isinstance(parsed.bozo_exception, SAXParseException)
    assert parsed.bozo_exception.getException().code == 27
    reader, feed_id = _reader(tmp_path, monkeypatch, body)
    with pytest.raises(app_rss.RssUrlRejected):
        reader.refresh_feeds()
    assert reader.get_feed_items() == []
    with reader._conn() as conn:
        assert rss.list_rss_feeds(conn)[0]['last_error'].startswith('RssUrlRejected:')


@pytest.mark.parametrize('extra', ['<other:extra/>', '<p:extra/>' * 1200])
def test_truncated_namespace_feed_never_stores_recovered_prefix(tmp_path, monkeypatch, extra):
    body = _body(extra).removesuffix('</channel></rss>').encode()
    reader, _ = _reader(tmp_path, monkeypatch, body)
    with pytest.raises(app_rss.RssUrlRejected):
        reader.refresh_feeds()
    assert reader.get_feed_items() == []


def test_deep_namespace_recovery_rejects_as_a_typed_source_failure(tmp_path, monkeypatch):
    depth = sys.getrecursionlimit() + 1
    body = _body('<other:extra/>' + '<nested>' * depth + 'x' + '</nested>' * depth).encode()
    reader, _ = _reader(tmp_path, monkeypatch, body)
    with pytest.raises(app_rss.RssUrlRejected):
        reader.refresh_feeds()
    assert reader.get_feed_items() == []


@pytest.mark.parametrize('declaration,first_code', [
    ('<!DOCTYPE rss>', 27),
    ('<!DOCTYPE rss [<!ENTITY local SYSTEM "file:///not-authorized">]>', 2),
])
def test_doctype_recovery_failure_does_not_abort_a_healthy_feed(tmp_path, monkeypatch, declaration, first_code):
    rejected = (declaration + _body('<other:extra/>')).encode()
    parsed = feedparser.parse(rejected)
    assert isinstance(parsed.bozo_exception, SAXParseException)
    assert parsed.bozo_exception.getException().code == first_code
    reader, failed_id = _reader(tmp_path, monkeypatch, rejected)
    with reader._conn() as conn:
        rss.upsert_rss_feed(conn, name='Healthy', url='https://example.org/healthy')
        conn.commit()
    monkeypatch.setattr(app_rss, '_fetch_public_url', lambda url, **k: (rejected if url.endswith('/feed') else _body(count=1).encode(), httpx.Headers()))
    result = reader.refresh_feeds()
    assert result['inserted'] == 1 and len(result['errors']) == 1
    assert result['errors'][0]['feed_id'] == failed_id
    assert result['errors'][0]['error'].startswith('RssUrlRejected:')
    assert [row['guid'] for row in reader.get_feed_items()] == ['story-0']


def test_namespace_validator_rejects_doctype_before_external_entity_resolution(tmp_path, monkeypatch):
    sentinel = tmp_path / 'source-private-sentinel.txt'
    sentinel.write_text('must not be read')
    body = (f'<!DOCTYPE rss [<!ENTITY local SYSTEM "{sentinel.as_uri()}">]>'
            + _body('<other:extra/>&local;')).encode()
    create_parser = app_rss.expat.ParserCreate
    resolved = []

    def parser_with_trap():
        parser = create_parser()

        def resolve(*args):
            resolved.append(args)
            raise AssertionError('XML attempted external entity resolution')

        parser.ExternalEntityRefHandler = resolve
        return parser

    monkeypatch.setattr(app_rss.expat, 'ParserCreate', parser_with_trap)
    with pytest.raises(app_rss.RssUrlRejected) as error:
        app_rss._rss_namespace_warning(body)
    assert str(error.value) == 'DOCTYPE is not admitted during XML namespace recovery'
    assert resolved == []


def test_namespace_validator_translates_unsupported_encoding():
    body = b'<?xml version="1.0" encoding="not-a-real-codec"?><rss/>'
    with pytest.raises(app_rss.RssUrlRejected):
        app_rss._rss_namespace_warning(body)


def test_namespace_issue_is_nonfatal_in_api_groups_and_cli_json(tmp_path, monkeypatch, capsys):
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from zotero_summarizer.api.errors import install_error_handlers
    from zotero_summarizer.api.routes import rss as routes
    from zotero_summarizer.cli import _feeds

    reader, _ = _reader(tmp_path, monkeypatch, _body('<other:extra/>', count=1).encode())
    settings = SimpleNamespace(triage_db_path=reader.triage_db_path)
    monkeypatch.setattr(routes, 'get_settings', lambda: settings)
    monkeypatch.setattr(_feeds.Settings, 'load', lambda **k: settings)
    app = FastAPI()
    app.include_router(routes.router)
    install_error_handlers(app)
    with TestClient(app) as client:
        response = client.post('/api/rss/refresh', json={})
        assert response.status_code == 200
        assert response.json()['inserted'] == 1 and response.json()['errors'] == []
        issue = client.get('/api/rss/feeds').json()['feeds'][0]['last_error']
    assert issue.startswith('RssNamespaceWarning:')
    assert reader.get_feed_groups()[0]['last_check_error'] == issue
    assert _feeds._feeds_list(argparse.Namespace(project_root=tmp_path, json=True)) == 0
    assert json.loads(capsys.readouterr().out)[0]['last_check_error'] == issue


@pytest.mark.parametrize('codec', ['utf-7', 'shift_jis', 'big5'])
def test_multibyte_namespace_validation_failure_continues_to_a_healthy_feed(tmp_path, monkeypatch, codec):
    text = f'<?xml version="1.0" encoding="{codec}"?>' + _body('<other:extra/>').replace('Café', 'Cafe')
    rejected = text.encode(codec)
    parsed = feedparser.parse(rejected)
    assert parsed.version == 'rss20' and isinstance(parsed.bozo_exception, SAXParseException)
    assert parsed.bozo_exception.getException().code == 27
    reader, failed_id = _reader(tmp_path, monkeypatch, rejected)
    with reader._conn() as conn:
        rss.upsert_rss_feed(conn, name='Healthy', url='https://example.org/healthy')
        conn.commit()
    monkeypatch.setattr(app_rss, '_fetch_public_url', lambda url, **k: (
        rejected if url.endswith('/feed') else _body(count=1).encode(), httpx.Headers()))
    result = reader.refresh_feeds()
    assert result['feeds'] == 2 and result['inserted'] == 1 and result['updated'] == 0
    assert len(result['errors']) == 1 and result['errors'][0]['feed_id'] == failed_id
    assert result['errors'][0]['error'].startswith('RssUrlRejected:')
    assert [row['guid'] for row in reader.get_feed_items()] == ['story-0']


def test_storage_value_error_is_not_classified_as_a_source_failure(tmp_path, monkeypatch):
    reader, failed_id = _reader(tmp_path, monkeypatch, _body('<other:extra/>', count=1).encode())
    with reader._conn() as conn:
        rss.upsert_rss_feed(conn, name='Healthy', url='https://example.org/healthy')
        conn.commit()
    original_upsert = rss.upsert_rss_item
    calls = []

    def upsert(conn, *, rss_feed_id, item):
        calls.append(rss_feed_id)
        if rss_feed_id == failed_id:
            raise ValueError('storage-programming-sentinel')
        return original_upsert(conn, rss_feed_id=rss_feed_id, item=item)

    monkeypatch.setattr(rss, 'upsert_rss_item', upsert)
    with pytest.raises(ValueError) as error:
        reader.refresh_feeds()
    assert error.value.args == ('storage-programming-sentinel',)
    assert calls == [failed_id]
    assert reader.get_feed_items() == []
