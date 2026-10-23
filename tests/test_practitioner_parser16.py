"""Synthetic parser/reader contract controls; no recovered public-capture claim."""
from __future__ import annotations

import asyncio
import difflib
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import socket
import xml.etree.ElementTree as ET

import feedparser
import httpx
import pytest

from zotero_summarizer.api.routes import rss as rss_routes
from zotero_summarizer.integrations import app_rss
from zotero_summarizer.integrations.app_rss import AppRssReader, _entry_to_item
from zotero_summarizer.models import SummarizeRequest

from _practitioner_inputs import practitioner_inputs as practitioner_inputs

Q = Path(__file__).resolve().parents[1]
FEEDS = ('https://hackernoon.com/tagged/ai-agents/feed',
         'https://hackernoon.com/tagged/agentic-ai/feed')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def retain(record_property, name, value):
    record_property(name, json.dumps(value, ensure_ascii=False))
    prefix = os.environ.get('ZS_PRACTITIONER_TEST_EVIDENCE')
    if prefix:
        path = Path(prefix).resolve()
        assert path.is_relative_to(Q / 'data/issue-validation/practitioner16')
        with path.with_name(path.name + '-' + name + '.json').open('x') as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)


def test_frozen_parser_and_actual_reader_request_roles(record_property, practitioner_inputs):
    body = practitioner_inputs.xml.read_bytes()
    assert sha(body) == practitioner_inputs.xml_sha
    raw_items = ET.fromstring(body).findall('./channel/item')
    parsed = feedparser.parse(body)
    cohort = json.loads(practitioner_inputs.cohort.read_text())['cases']
    roles = json.loads((practitioner_inputs.grader / 'source-roles.json').read_text())['cases']
    dist = importlib.metadata.distribution('feedparser')
    rows = []

    assert parsed.version == 'rss20' and not parsed.bozo
    assert len(raw_items) == len(parsed.entries) == 20
    for case, role in zip(cohort, roles, strict=True):
        index = role['rss_item_index'] - 1
        raw = raw_items[index]
        entry = parsed.entries[index]
        item = _entry_to_item(entry, feed={'id': 1, 'name': 'DIAGNOSTIC main capture'},
                              feed_title=parsed.feed.title)
        raw_description = raw.findtext('description')
        raw_html = raw.findtext('{http://purl.org/rss/1.0/modules/content/}encoded')
        binding = case['abstract_binding']
        description_bytes = Path(binding['path']).read_bytes()
        html_binding = role['rss_full_html']
        html_bytes = Path(html_binding['path']).read_bytes()
        assert sha(description_bytes) == binding['sha256']
        assert sha(html_bytes) == html_binding['sha256']
        assert raw_description == description_bytes.decode() == case['abstract']
        assert raw_html == html_bytes.decode()
        assert entry.summary == raw_description == item['abstract']
        assert item['stable_feed_key'] == case['stable_feed_key_derivation']
        assert item['url'] == case['url'] and item['doi'] == ''
        assert item['guid'] == case['guid']
        req = SummarizeRequest(title=item['title'], doi=item['doi'] or None,
                               abstract=item['abstract'], pdf_path='')
        content = entry.content[0]['value']
        assert item['title'] == case['title']
        assert raw_html != content == practitioner_inputs.parsed_html[case['case_id']]
        assert role['case_id'] == case['case_id']
        edits = [{'tag': tag, 'raw': raw_html[a:b], 'parsed': content[c:d]}
                 for tag, a, b, c, d in difflib.SequenceMatcher(
                     None, raw_html, content, autojunk=False).get_opcodes() if tag != 'equal']
        rows.append({'case_id': case['case_id'], 'raw_title': raw.findtext('title'),
                     'parser_title': entry.title, 'mapped_title': item['title'],
                     'request': req.model_dump(mode='json'), 'stable_feed_key': item['stable_feed_key'],
                     'raw_description_chars': len(raw_description), 'abstract_chars': len(req.abstract),
                     'raw_html_chars': len(raw_html), 'parsed_html_chars': len(content),
                     'parsed_html_sha256': sha(content.encode()), 'content_type': entry.content[0]['type'],
                     'parser_html_edits': edits, 'later_web_pdf': None})
    receipt = {'scope': 'synthetic mechanical inputs + installed parser/AppRssReader; NOT historical capture',
               'version': dist.version, 'license': dist.metadata.get('License'),
               'license_classifiers': [v for v in dist.metadata.get_all('Classifier', []) if 'License' in v],
               'parser_file': feedparser.__file__, 'raw_items': 20, 'selected_cases': 6,
               'unselected_items': 14, 'cases': rows}
    assert tuple(row['case_id'] for row in rows) == practitioner_inputs.selected_ids
    assert practitioner_inputs.selected_ids == ('public-03', 'public-08', 'public-10',
                                                'public-14', 'public-15', 'public-20')
    retain(record_property, 'parser-roles', receipt)


def test_actual_reader_sanitizes_parsed_fields_without_content_promotion():
    entry = feedparser.parse(b'''<rss version="2.0"><channel><title>fixture</title><item>
    <guid>synthetic-guid</guid><title>A&#xE0001; title</title><link>https://example.org/story</link>
    <description> Short&#xE0001; abstract&#x200B; </description>
    <content:encoded xmlns:content="http://purl.org/rss/1.0/modules/content/">FULL HTML ONLY</content:encoded>
    </item></channel></rss>''').entries[0]

    item = _entry_to_item(entry, feed={'id': 1, 'name': 'fixture'}, feed_title='fixture')

    assert item['title'] == 'A title'
    assert item['abstract'] == 'Short abstract\u200b'
    assert entry.content[0]['value'] == 'FULL HTML ONLY'
    assert 'FULL HTML ONLY' not in item['abstract']


def test_requested_feed_registration_empty_capture_ingestion_reopen(
    tmp_path, monkeypatch, record_property, practitioner_inputs,
):
    db = tmp_path / 'data/triage.db'
    settings = type('OwnedSettings', (), {'triage_db_path': db,
                    'zotero_data_dir': tmp_path / 'data/no-zotero'})()
    monkeypatch.setattr(rss_routes, 'get_settings', lambda: settings)
    monkeypatch.setattr(app_rss.socket, 'getaddrinfo', lambda *a, **kw: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443))])
    monkeypatch.setattr(app_rss, 'offline_requested', lambda: False)
    captures = {url: (practitioner_inputs.capture / f'feed-{i}.xml').read_bytes()
                for i, url in enumerate(FEEDS)}
    expected = practitioner_inputs.feed_shas
    for url, digest in zip(FEEDS, expected, strict=True):
        assert sha(captures[url]) == digest
        assert len(feedparser.parse(captures[url]).entries) == 0
    fetched = []

    def fetch(url, *, timeout):
        fetched.append(url)
        return captures[url], httpx.Headers()

    monkeypatch.setattr(app_rss, '_fetch_public_url', fetch)
    for i, url in enumerate(FEEDS):
        asyncio.run(rss_routes.add_feed(rss_routes.RssFeedRequest(name=f'Requested {i}', url=url)))

    result = AppRssReader(db).refresh_feeds(max_feeds=2)
    reopened = AppRssReader(db)

    assert result == {'feeds': 2, 'inserted': 0, 'updated': 0, 'errors': []}
    assert set(fetched) == set(FEEDS)
    assert {f['url'] for f in reopened.get_feed_groups()} == set(FEEDS)
    assert reopened.get_feed_items(limit=None) == []
    retain(record_property, 'requested-feeds', {'scope': 'mocked HTTP synthetic empty feeds, clean SQLite',
           'result': result, 'feeds': reopened.get_feed_groups(), 'items': [], 'live_network_calls': 0})
