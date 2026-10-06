// @vitest-environment jsdom
import React from 'react';
import { render, cleanup } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import PaperReview from './PaperReview.jsx';

afterEach(cleanup);
const deep = {
  digest: { tldr: 'Synthetic contribution.', read_decision: 'skim', read_why: 'Synthetic rationale.',
    key_findings: ['Synthetic finding.'], methods: 'Synthetic method.', limitations: 'Synthetic limitation.',
    key_weakness: 'Synthetic weakness.', executive_summary: 'Unique secondary summary.' },
  quality: { quality_band: 'uncertain', red_flags: ['Flag one.', 'Flag two.', 'Flag three.', 'Flag four.'],
    overstatements: ['Overstatement.'], missing_critical: ['external_validation'], coverage_applicable: 0 },
};
it('preserves a legacy assessment when an empty digest object is cached', () => {
  const { container } = render(<PaperReview flat deep={{ digest: {}, goal_summaries: [],
    quality: { grade: 'B', verdict: 'Legacy assessment.' } }} />);
  expect(container.textContent).toContain('Legacy assessment.');
  expect(container.textContent).toContain('Older review');
  expect(container.querySelectorAll('h2')).toHaveLength(0);
});

it('orders the full reading flow and keeps every material caveat outside disclosures', () => {
  const { container } = render(<PaperReview deep={deep} flat />);
  const headings = [...container.querySelectorAll('h2')].map(el => el.textContent);
  expect(headings).toEqual(['Contribution', 'Caveats and coverage', 'Findings and applicability', 'Methods and limitations', 'Assessment and reference']);
  for (const text of ['Flag four.', 'Overstatement.', 'Synthetic weakness.', 'Synthetic method.', 'Synthetic limitation.', 'Validated on independent / held-out data']) {
    const node = [...container.querySelectorAll('*')].find(el => el.textContent === text);
    expect(node, text).toBeTruthy();
    expect(node.closest('details'), text).toBeNull();
  }
  for (const text of ['Synthetic rationale.', 'Synthetic finding.']) {
    expect(container.textContent.split(text).length - 1).toBe(1);
  }
  expect(container.textContent).toContain('Unique secondary summary.');
});
it('retains the entire original goal label and source text on disclosure', () => {
  const goal = 'A complete synthetic goal label longer than four words';
  const summary = Array.from({ length: 65 }, (_, i) => `word${i}`).join(' ');
  const { container } = render(<PaperReview flat deep={{ goal_summaries: [{ goal, summary,
    retrieval_state: 'hit', relevant: true, supporting_quotes: ['Synthetic quote.'] }] }} />);
  expect(container.textContent).toContain(goal);
  expect(container.textContent).toContain(summary);
  expect(container.textContent).toContain('Synthetic quote.');
});

it('bounds the opening to three unique 30-word findings and retains the source tail', () => {
  const summary = (n) => Array.from({ length: 45 }, (_, i) => `claim${n}word${i}`).join(' ');
  const goals = Array.from({ length: 6 }, (_, i) => ({ goal: `Synthetic goal ${i}`, retrieval_state: 'hit',
    relevant: i !== 5, abstained: i === 5, summary: summary(i === 1 ? 0 : i),
    supporting_quotes: ['One shared synthetic quote.'], key_sections: ['Synthetic Methods'] }));
  const { container } = render(<PaperReview flat deep={{ goal_summaries: goals }} />);
  const paragraphs = [...container.querySelectorAll('li > p')].filter(el => !el.closest('details'));
  expect(paragraphs).toHaveLength(3);
  expect(paragraphs.reduce((total, el) => total + el.textContent.split(/\s+/).length, 0)).toBe(90);
  expect(container.textContent).toContain('For: Synthetic goal 0; Synthetic goal 1');
  expect(container.textContent).toContain(summary(5));
  expect(container.textContent).toContain('○ abstained');
  expect(container.textContent).toContain('grounded summary withheld');
  expect(container.querySelectorAll('blockquote')).toHaveLength(6);
  expect(container.textContent).toContain('Synthetic Methods');
});

it('preserves exact source variants without conflating distinct conclusions sharing a quote', () => {
  const { container } = render(<PaperReview flat deep={{ goal_summaries: [
    { goal: 'First', retrieval_state: 'hit', relevant: true, summary: 'A result. A result.', supporting_quotes: ['Shared quote.'] },
    { goal: 'Second', retrieval_state: 'hit', relevant: true, summary: 'A different result.', supporting_quotes: ['Shared quote.'] },
  ] }} />);
  expect(container.textContent).toContain('A result. A result.');
  const visibleFindings = [...container.querySelectorAll('li > p')].filter(el => !el.closest('details')).map(el => el.textContent);
  expect(visibleFindings).toEqual(['A result.', 'A different result.']);
});

it('distinguishes abstention, unsupported evidence and failed retrieval', () => {
  const { container } = render(<PaperReview flat deep={{ goal_summaries: [
    { goal: 'Withheld', retrieval_state: 'hit', abstained: true, relevant: false, summary: 'Retained source text.' },
    { goal: 'Unsupported', retrieval_state: 'hit', relevant: false, summary: 'Unsupported source text.' },
    { goal: 'Missing', retrieval_state: 'miss' },
    { goal: 'Unavailable', retrieval_state: 'not_retrieved' },
  ] }} />);
  for (const text of ['○ abstained', '○ not supported', '○ not addressed', '⚠ not retrieved']) {
    expect(container.textContent).toContain(text);
  }
  expect(container.textContent).toContain('Retained source text.');
  expect(container.textContent).toContain('Contribution not recorded');
});

it('keeps compact content folded and non-paper scientific criteria suppressed', () => {
  const { container } = render(<PaperReview flat compact deep={{ ...deep, quality: { ...deep.quality, basis: 'non_paper', grade: 'A' } }} />);
  expect(container.querySelector('.review-reading')).toBeNull();
  expect(container.textContent).not.toContain('Quality A');
  expect(container.textContent).toContain('Not peer-reviewed');
  const contribution = [...container.querySelectorAll('p')].find(el => el.textContent === deep.digest.tldr);
  expect(contribution.closest('details')).toBeTruthy();
});
