import html
import re

import pytest

from zotero_summarizer.models import SummarizeResponse
from zotero_summarizer.services.zotero._notes import build_triage_note_html


@pytest.mark.parametrize('rationale', ['   ', '\t\n', '\u00a0', '\u2003', ''])
def test_first_nonblank_escaped_and_immutable(rationale):
    summary = SummarizeResponse(relevance_score=4, triage_rationale=rationale, executive_summary='  Study <x> & y.  ')
    before = summary.model_dump()
    note = build_triage_note_html('T', summary)
    assert re.search(r'<p>(.*?)</p>', note).group(1) == html.escape('Study <x> & y.')
    assert summary.model_dump() == before


def test_optional_supported_sections_render():
    fields = dict(controversial_points='Controversy <x>.', industry_academy_impact='Impact.',
                  unknown_unknowns='Unknown.', implementation_quickstart='Implement.')
    note = build_triage_note_html('T', SummarizeResponse(relevance_score=4, executive_summary="Overview.", triage_rationale="Fit.", **fields))
    for value in fields.values():
        assert html.escape(value) in note


@pytest.mark.parametrize('rationale,overview,fallback', [(' \u2003', 'Study.', False), ('', '', True)])
def test_metrics_follow_rendered_fallback(rationale, overview, fallback):
    from zotero_summarizer.services.zotero._notes import triage_note_metrics

    summary = SummarizeResponse(relevance_score=4, triage_rationale=rationale,
                                executive_summary=overview)
    note = build_triage_note_html('T', summary)
    metrics = triage_note_metrics(note, summary)
    assert metrics['generic_fallback'] is fallback
    assert metrics['characters'] == len(' '.join(html.unescape(re.sub(r'<[^>]+>', ' ', note)).split()))
    assert metrics['html_characters'] == len(note)
    assert metrics['sections'] == note.count('<h2>')
    assert metrics['words'] == len(html.unescape(re.sub(r'<[^>]+>', ' ', note)).split())


def test_long_sections_keep_whole_qualified_sentences_and_exact_artifacts():
    from zotero_summarizer.models import MethodAndCode

    first = 'Only in vitro, accuracy was 3.14 with no clinical validation.'
    long = first + ' ' + ' '.join(['additional'] * 150) + '.'
    url = 'https://example.org/v1.2/source?value=3.14&mode=full'
    fields = {name: long for name in (
        'triage_rationale', 'executive_summary', 'methods', 'limitations',
        'relevance_to_research', 'controversial_points', 'industry_academy_impact',
        'unknown_unknowns', 'implementation_quickstart')}
    summary = SummarizeResponse(relevance_score=4, **fields, key_findings=[long],
                               key_sections_to_read=[long], method_and_code=MethodAndCode(
                                   what_it_does=long, artifacts=[url]))
    before = summary.model_dump()
    note = build_triage_note_html('T', summary, text_word_budget=20, list_word_budget=20)
    assert first not in note
    assert 'additional' not in note
    assert 'Omitted over-budget text; see the full saved summary.' in note
    assert html.escape(url) in note
    assert summary.model_dump() == before


def test_unbroken_overbudget_sentence_is_explicitly_omitted():
    sentence = ' '.join(['qualified'] * 130) + '.'
    summary = SummarizeResponse(relevance_score=4, executive_summary="Overview.", triage_rationale="Fit.", methods=sentence,
                               key_findings=[sentence])
    note = build_triage_note_html('T', summary, text_word_budget=120, list_word_budget=60)
    assert 'qualified' not in note
    assert 'Omitted over-budget text; see the full saved summary.' in note


def test_four_note_types_upsert_twice_preserve_manual_note_and_keys(tmp_path):
    import sqlite3

    from tests._zotero_fixtures import add_library_item, build_zotero_db
    from zotero_summarizer.integrations.zotero_write import ZoteroWriter
    from zotero_summarizer.models import PaperDigest
    from zotero_summarizer.services.zotero._notes import (
        DIGEST_NOTE_MARKER, USER_NOTE_MARKER, VERDICT_NOTE_MARKER,
        build_digest_note_html, build_user_note_html, build_verdict_note_html,
    )

    db = build_zotero_db(tmp_path / 'isolated-owner')
    parent = add_library_item(db, item_key='PARENT', title='Paper')
    manual = add_library_item(db, item_key='MANUAL', title='Personal', item_type='note')
    with sqlite3.connect(db) as conn:
        conn.execute('INSERT INTO itemNotes(itemID, parentItemID, note) VALUES (?, ?, ?)',
                     (manual, parent, '<p>My unmarked personal text.</p>'))
    writer = ZoteroWriter(db.parent)
    summary = SummarizeResponse(relevance_score=4, executive_summary='Study.', triage_rationale='Fit.')
    notes = {
        'zs:note_type=triage': build_triage_note_html('Paper', summary),
        DIGEST_NOTE_MARKER: build_digest_note_html(PaperDigest(tldr='Digest.')),
        VERDICT_NOTE_MARKER: build_verdict_note_html('must_read', 'Decision.'),
        USER_NOTE_MARKER: build_user_note_html('Thinking.'),
    }
    changes = [dict(id=i, item_key='PARENT', change_type='upsert_note',
                    payload_json=dict(marker=marker, note_html=body))
               for i, (marker, body) in enumerate(notes.items())]
    snapshots = []
    for _ in range(2):
        result = writer.apply_changes(changes, create_backup=False)
        assert result['failed'] == []
        with sqlite3.connect(db) as conn:
            snapshots.append(conn.execute(
                'SELECT i.key, n.note FROM itemNotes n JOIN items i USING(itemID) ORDER BY i.key'
            ).fetchall())
    assert snapshots[0] == snapshots[1]
    assert len(snapshots[1]) == 5
    assert ('MANUAL', '<p>My unmarked personal text.</p>') in snapshots[1]
    for marker, body in notes.items():
        expected = '<div class="zotero-note znv1">' + body + '</div>'
        assert sum(text == expected and marker in text for _, text in snapshots[1]) == 1


def test_sentence_budget_does_not_split_decimal_or_source_url():
    source = 'https://example.org/v1.2/model?threshold=3.14'
    first = f'Only simulations scored 3.14 using {source} without clinical validation.'
    summary = SummarizeResponse(relevance_score=4, executive_summary='Overview.',
                               triage_rationale='Fit.', methods=first + ' More evidence is needed.')
    note = build_triage_note_html('T', summary, text_word_budget=len(first.split()))
    assert html.escape(first) not in note
    assert 'More evidence' not in note
    assert 'Omitted over-budget text; see the full saved summary.' in note


@pytest.mark.parametrize('text', [
    'Patients were assessed by Dr. Smith and no benefit was established.',
    'We used e.g. simulations and no independent validation was performed.',
    'Participants saw Prof. Jones and no difference was established.',
    'Measurements were approx. equal but clinical conclusions remain unsupported.',
    'Исследование провёл И. И. Иванов, но результат не был подтверждён.',
])
def test_budget_cannot_publish_an_abbreviation_prefix(text):
    from zotero_summarizer.services.zotero._notes import _bounded_note_text

    assert _bounded_note_text(text, 6) == 'Omitted over-budget text; see the full saved summary.'
    assert _bounded_note_text(text, len(text.split())) == text
    assert _bounded_note_text(text, len(text.split()) + 1) == text


def test_default_note_preserves_long_paper_specific_fields():
    sentence = ' '.join(['qualified'] * 130) + '.'
    summary = SummarizeResponse(relevance_score=4, executive_summary='Overview.',
                               triage_rationale='Fit.', methods=sentence, key_findings=[sentence])
    note = build_triage_note_html('T', summary)
    assert note.count(sentence) == 2
    assert 'Omitted over-budget text' not in note


def test_default_note_retains_all_distinct_list_values():
    findings = [f'Distinct finding {i} preserves a limitation.' for i in range(8)]
    sections = [f'Section {i}: original evidence.' for i in range(8)]
    tags = [f'topic-{i}' for i in range(8)]
    summary = SummarizeResponse(relevance_score=4, executive_summary='Overview.', triage_rationale='Fit.',
                               key_findings=findings, key_sections_to_read=sections, tags=tags)
    note = build_triage_note_html('T', summary)
    for value in [*findings, *sections, *tags]:
        assert html.escape(value) in note
    assert 'Shortened' not in note


def test_empty_list_and_invalid_budgets():
    summary = SummarizeResponse(relevance_score=4, executive_summary='Overview.',
                               triage_rationale='Fit.', key_findings=[' '] * 7)
    assert 'Key findings' not in build_triage_note_html('T', summary)
    with pytest.raises(ValueError, match='positive'):
        build_triage_note_html('T', summary, text_word_budget=0)
