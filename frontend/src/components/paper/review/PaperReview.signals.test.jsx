import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import PaperReview from './PaperReview.jsx';
import { decisiveRows } from './briefModel.js';

const digest = {
  tldr: 'A reusable concept.', read_decision: 'skim', read_why: 'Idea preserved; writing friction is high.',
  read_parts: ['§2, Fig. 1'], skip_parts: ['Appendix A'], original_value: 'Inspect a decisive comparison.',
  novelty: 5, significance: 4, writing_friction: 'high', writing_reasons: ['§2 uses state before defining it.'],
  grade: 'A',
};
const quality = {
  quality_band: 'neutral', grade: 'C', basis: 'full_text', paper_type: 'position',
  coverage_standard: 'Argument quality (Wachsmuth)', coverage_met: 2, coverage_applicable: 4,
  rubric: { thesis: 'yes', evidence_cited: 'no', counterarguments: 'na', scope_match: 'no' },
};

describe('orthogonal reviewed signals', () => {
  it.each([{ compact: true }, {}, { flat: true }])('accepts an omitted legacy abstention flag consistently in %j', props => {
    const deep = { digest, quality, goal_summaries: [
      { goal_id: 'legacy', goal_label: 'Legacy goal', retrieval_state: 'hit', relevant: true,
        summary: 'Retained supported summary.', key_sections: [] },
    ] };
    const html = renderToStaticMarkup(<PaperReview deep={deep} {...props} />);
    expect(html).toContain('Supported goal match recorded.');
    expect(html).not.toContain('Goal match unknown.');
  });

  it.each([{ compact: true }, {}, { flat: true }])('keeps applicability in one home and uses the shared legacy goal predicate in %j', props => {
    const relevance = 'Unique literal applicability for this saved review.';
    const deep = { digest: { ...digest, relevance }, quality, goal_summaries: [
      { goal_id: 'g', goal_label: 'Bounded retrieval', retrieval_state: 'hit', relevant: true,
        summary: 'A supported saved finding.', key_sections: [] },
    ] };
    const before = JSON.stringify(deep);
    const html = renderToStaticMarkup(<PaperReview deep={deep} {...props} />);
    expect(html.split(relevance)).toHaveLength(2);
    expect(html).toContain('Supported goal match recorded.');
    expect(html).not.toContain('Goal match unknown.');
    expect(JSON.stringify(deep)).toBe(before);
  });

  it.each([
    ['original_value_not_assessed', 'Original-only value was not assessed; the reading decision is withheld.'],
    ['reading_targets_not_assessed', 'Specific reading targets were not recorded; the reading decision is withheld.'],
  ].flatMap(([flag, text]) => [{ compact: true }, {}, { flat: true }].map(props => ({ flag, text, props }))))(
    'explains the recorded policy reason $flag in $props', ({ flag, text, props }) => {
      const deep = { digest: { ...digest, read_decision: '', read_parts: [], original_value: '' }, quality,
        reading_policy_flags: [flag], model_read_decision: 'read' };
      const before = JSON.stringify(deep);
      const html = renderToStaticMarkup(<PaperReview deep={deep} {...props} />);
      expect(html).toContain(text);
      expect(html).not.toContain('>READ<');
      expect(JSON.stringify(deep)).toBe(before);
    },
  );
  it.each([{ compact: true }, {}, { flat: true }])('shares policy output across review modes %j', props => {
    const deep = { digest: { ...digest, grade: 'C' }, quality,
      model_digest: { ...digest, read_decision: 'read' } };
    const original = JSON.stringify(deep);
    const html = renderToStaticMarkup(<PaperReview deep={deep} {...props} />);
    for (const text of ['SKIM', 'Idea: high', 'Evidence: Neutral', 'Writing: high',
      digest.writing_reasons[0], '§2, Fig. 1', 'Appendix A', digest.original_value]) {
      expect(html).toContain(text);
    }
    expect(html.split(digest.writing_reasons[0])).toHaveLength(2);
    expect(html).toContain('Argument quality (Wachsmuth)');
    expect(html).toContain('2/4 applicable items met');
    expect(html).not.toContain('Quality A');
    expect(html).toContain(props.compact ? '>C</span>' : 'Quality C');
    expect(html).not.toContain('Ablations isolate');
    expect(html).not.toContain('Validated on independent');
    expect(html).not.toContain('≥6 grounded');
    expect(JSON.stringify(deep)).toBe(original);
  });

  it.each(['flag', 'uncertain', 'highlight', 'neutral'])('uses only supplied applicable checks for %s', band => {
    const summary = decisiveRows(quality.rubric, band);
    expect(summary.caption).toBe('1/3 applicable checks met');
    const thesis = { ok: true, label: 'States a clear thesis / position' };
    const evidence = { ok: false, label: 'Supports premises with cited evidence' };
    const scope = { ok: false, label: 'Matches claim scope to the evidence offered' };
    const expected = { flag: [evidence, scope], neutral: [evidence, scope],
      highlight: [thesis], uncertain: [thesis, evidence, scope] };
    expect(summary.rows).toEqual(expected[band]);
    expect(summary.rows.some(row => row.label === 'counterarguments')).toBe(false);
    expect(decisiveRows({}, band).rows).toEqual([]);
  });

  it('does not invent a neutral writing assessment or scientific grade for unknown data', () => {
    const html = renderToStaticMarkup(<PaperReview deep={{
      digest: { ...digest, grade: '', writing_friction: null, writing_reasons: [], read_decision: '' },
      quality: { quality_band: 'uncertain', basis: 'not_assessed', grade: '' },
    }} flat />);
    expect(html).toContain('REVIEW');
    expect(html).toContain('Writing: not assessed');
    expect(html).not.toContain('Quality A');
    expect(html).not.toContain('>SKIP<');
  });

  it('suppresses scientific criteria for non-papers but preserves the concept and reading action', () => {
    const html = renderToStaticMarkup(<PaperReview deep={{ digest,
      quality: { ...quality, basis: 'non_paper', grade: '', quality_band: '' },
    }} />);
    expect(html).toContain('SKIM');
    expect(html).toContain('Evidence: not applicable');
    expect(html).toContain(digest.tldr);
    expect(html).not.toContain('Argument quality');
    expect(html).not.toContain('scope match');
    expect(html).not.toContain('Quality A');
  });

  it.each([{ compact: true }, {}, { flat: true }])('keeps a failed new assessment ungraded in %j', props => {
    const deep = { digest: { ...digest, grade: '' }, model_digest: digest,
      quality: { quality_band: 'uncertain', basis: 'not_assessed', grade: '' } };
    const html = renderToStaticMarkup(<PaperReview deep={deep} {...props} />);
    expect(html).not.toContain('title="Saved quality grade"');
    expect(html).not.toContain('Quality A');
    expect(html).toContain('Evidence: Uncertain');
    expect(deep.model_digest.grade).toBe('A');
  });

  it.each([{ compact: true }, {}, { flat: true }].flatMap(props =>
    [null, '2026-10-14T00:00:00Z'].map(reviewedAt => ({ props, reviewedAt })),
  ))('does not treat a retained review flag as a native write receipt %j', ({ props, reviewedAt }) => {
    const deep = { digest, quality, reviewed_at: reviewedAt, zotero_note_written: true };
    const before = JSON.stringify(deep);
    const html = renderToStaticMarkup(<PaperReview deep={deep} {...props} />);
    expect(html).not.toContain('saved to Zotero');
    expect(html.includes('>reviewed ')).toBe(Boolean(reviewedAt));
    expect(html).toContain('SKIM');
    expect(JSON.stringify(deep)).toBe(before);
  });

  it('retains a recorded note error without inventing a successful native write', () => {
    const deep = { digest, quality, zotero_note_written: true, zotero_note_error: 'Historical note error.' };
    const before = JSON.stringify(deep);
    const html = renderToStaticMarkup(<PaperReview deep={deep} flat />);
    expect(html).toContain('note not written: Historical note error.');
    expect(html).not.toContain('saved to Zotero');
    expect(JSON.stringify(deep)).toBe(before);
  });

  it('does not reclassify the server-owned decision locally', () => {
    const html = renderToStaticMarkup(<PaperReview deep={{ digest: { ...digest, read_decision: 'read' }, quality }} />);
    expect(html).toContain('>READ<');
    expect(html).not.toContain('>SKIM<');
  });
});
