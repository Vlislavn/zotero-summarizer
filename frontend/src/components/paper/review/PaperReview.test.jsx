import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import PaperReview from './PaperReview.jsx';

it('keeps questions distinct from assertions and every quote available', () => {
  const html = renderToStaticMarkup(<PaperReview deep={{ goal_summaries: [
    { goal: 'Hypothesis', retrieval_state: 'hit', summary: 'The drug is safe?',
      supporting_quotes: ['First observation.', 'Second observation.'] },
    { goal: 'Conclusion', retrieval_state: 'hit', summary: 'The drug is safe.',
      supporting_quotes: ['Separate evidence.'] },
  ] }} />);
  expect(html).toContain('The drug is safe?');
  expect(html).toContain('The drug is safe.');
  expect(html).not.toContain('For: Hypothesis; Conclusion');
  expect(html).toContain('Second observation.');
});

it('does not merge a hazard-ratio point estimate with a range', () => {
  const html = renderToStaticMarkup(<PaperReview deep={{ goal_summaries: [
    { goal: 'Point', retrieval_state: 'hit', summary: 'Hazard ratio 1.2.' },
    { goal: 'Range', retrieval_state: 'hit', summary: 'Hazard ratio 1–2.' },
  ] }} />);
  expect(html).toContain('Hazard ratio 1.2.');
  expect(html).toContain('Hazard ratio 1–2.');
  expect(html).not.toContain('For: Point; Range');
});

it('keeps case-distinct quantitative findings separate', () => {
  const html = renderToStaticMarkup(<PaperReview deep={{ goal_summaries: [
    { goal: 'Enrolled women', retrieval_state: 'hit', summary: 'N=120 participants.' },
    { goal: 'Enrolled men', retrieval_state: 'hit', summary: 'n=120 participants.' },
  ] }} />);
  expect(html).toContain('N=120 participants.');
  expect(html).toContain('n=120 participants.');
  expect(html).not.toContain('For: Enrolled women; Enrolled men');
});

it('does not claim a retrieved goal is relevant when its evidence says otherwise', () => {
  const html = renderToStaticMarkup(<PaperReview deep={{ goal_summaries: [
    { goal: 'External clinical validation', retrieval_state: 'hit', relevant: false,
      summary: 'No external clinical validation was performed.', score: 0,
      supporting_quotes: ['Evaluation used only the development cohort.'] },
  ] }} />);

  expect(html).toContain('Evidence did not support this goal');
  expect(html).toContain('○ not supported');
  expect(html).not.toContain('● addressed');
  expect(html).not.toContain('Relevant to this goal');
  expect(html).toContain('Evaluation used only the development cohort.');
});

it('renders empty and degraded summaries without inventing findings', () => {
  const html = renderToStaticMarkup(<PaperReview deep={{ goal_summaries: [
    { goal: 'No grounded summary', retrieval_state: 'hit', summary: '' },
    { goal: 'Unavailable retrieval', retrieval_state: 'not_retrieved', summary: '' },
  ] }} />);
  expect(html).toContain('grounded summary withheld');
  expect(html).toContain('retrieval degraded — not assessed');
  expect(html).not.toContain('<p class="text-slate-600"></p>');
});

it('deduplicates repeated goal conclusions but keeps distinct evidence and overflow', () => {
  const repeated = 'A shared conclusion explains the clinical result.';
  const goals = Array.from({ length: 5 }, (_, i) => ({
    goal: `Goal ${i}`, retrieval_state: 'hit', relevant: true, score: 2.5,
    summary: i < 2 ? repeated : `Distinct clinical finding ${i}.`,
    supporting_quotes: [`Evidence for goal ${i}.`],
  }));
  const html = renderToStaticMarkup(<PaperReview deep={{ goal_summaries: goals }} />);
  expect((html.match(/A shared conclusion explains the clinical result/g) || []).length).toBe(1);
  expect(html).toContain('For: Goal 0; Goal 1');
  expect(html).toContain('Evidence for goal 4.');
  expect(html).toContain('more findings');
  expect((html.match(/role="meter"/g) || []).length).toBe(5);
});

describe('withheld reading decision', () => {
  it.each([null, { read_decision: '', read_why: 'Goals were not assessed.' }])(
    'does not reconstruct SKIP from an unknown goal board (%j)', (digest) => {
      const html = renderToStaticMarkup(<PaperReview deep={{
        digest,
        quality: { quality_band: 'neutral' },
        goal_summaries: [{ goal: 'Evaluation', retrieval_state: 'not_retrieved', abstained: true }],
      }} />);

      expect(html).toContain('REVIEW');
      expect(html).not.toContain('none of your research goals are addressed');
      expect(html).not.toContain('>SKIP<');
    },
  );
});
