"""Synthetic mechanical inputs, not restored historical/public RSS captures."""
from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import pytest

SELECTED = (3, 8, 10, 14, 15, 20)
RAW_HTML = '<p>FORBIDDEN_GENERATOR_SENTINEL<br/>HTML ONLY {index}</p>'
PARSED_HTML = '<p>FORBIDDEN_GENERATOR_SENTINEL<br />HTML ONLY {index}</p>'


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


@pytest.fixture
def practitioner_inputs(tmp_path):
    root = tmp_path / 'synthetic-practitioner-inputs'
    capture, generator, grader = (root / name for name in ('capture', 'generator', 'grader-only'))
    for directory in (capture, generator, grader):
        directory.mkdir(parents=True)
    rss = ET.Element('rss', {'version': '2.0', 'xmlns:content': 'http://purl.org/rss/1.0/modules/content/'})
    channel = ET.SubElement(rss, 'channel')
    ET.SubElement(channel, 'title').text = 'Synthetic mechanical fixture, not a field capture'
    for index in range(1, 21):
        item = ET.SubElement(channel, 'item')
        ET.SubElement(item, 'title').text = f'Synthetic item {index}'
        ET.SubElement(item, 'guid', {'isPermaLink': 'false'}).text = f'fixture-guid-{index}'
        ET.SubElement(item, 'link').text = f'https://hackernoon.com/synthetic-contract-fixture-{index}'
        ET.SubElement(item, 'description').text = f'Synthetic description only {index}.'
        ET.SubElement(item, 'content:encoded').text = RAW_HTML.format(index=index)
    xml = capture / 'main-feed.xml'
    xml.write_bytes(ET.tostring(rss, encoding='utf-8'))
    xml_sha = _digest(xml.read_bytes())
    cases, roles = [], []
    for index in SELECTED:
        case_id = f'public-{index:02d}'
        description = generator / f'{case_id}.rss-description.txt'
        description.write_text(f'Synthetic description only {index}.')
        html = grader / f'{case_id}.rss-content.html'
        html.write_text(RAW_HTML.format(index=index))
        cases.append({'case_id': case_id, 'title': f'Synthetic item {index}', 'doi': None,
                      'guid': f'fixture-guid-{index}',
                      'url': f'https://hackernoon.com/synthetic-contract-fixture-{index}',
                      'input_mode': 'rss_description', 'abstract': description.read_text(),
                      'abstract_binding': {'path': str(description), 'sha256': _digest(description.read_bytes()),
                                           'bytes': description.stat().st_size},
                      'stable_feed_key_derivation': 'feed:g:' + _digest(f'fixture-guid-{index}'.encode()),
                      'provenance': {'path': str(xml), 'sha256': xml_sha,
                                     'xml_locator': f'rss/channel/item[{index}]'}})
        roles.append({'case_id': case_id, 'rss_item_index': index,
                      'rss_full_html': {'path': str(html), 'sha256': _digest(html.read_bytes())}})
    cohort = generator / 'cohort.json'
    _json(cohort, {'schema_version': 1, 'cases': cases})
    _json(grader / 'source-roles.json', {'cases': roles})
    feed_shas = []
    for index in range(2):
        path = capture / f'feed-{index}.xml'
        path.write_text(f'<rss version="2.0"><channel><title>Synthetic empty {index}</title></channel></rss>')
        feed_shas.append(_digest(path.read_bytes()))
    return SimpleNamespace(capture=capture, generator=generator, grader=grader, cohort=cohort,
                           cohort_sha=_digest(cohort.read_bytes()), xml=xml, xml_sha=xml_sha,
                           feed_shas=tuple(feed_shas), selected_ids=tuple(c['case_id'] for c in cases),
                           parsed_html={f'public-{i:02d}': PARSED_HTML.format(index=i) for i in SELECTED})
