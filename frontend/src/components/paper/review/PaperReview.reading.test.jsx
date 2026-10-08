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
  const findings = [...container.querySelectorAll('details')].find(el => el.querySelector(':scope > summary')?.textContent.includes('Goal findings'));
  expect(findings.open).toBe(false);
  const paragraphs = [...findings.querySelectorAll('li > p')].filter(el => el.closest('details') === findings);
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
  const findings = [...container.querySelectorAll('details')].find(el => el.querySelector(':scope > summary')?.textContent.includes('Goal findings'));
  const visibleFindings = [...findings.querySelectorAll('li > p')].filter(el => el.closest('details') === findings).map(el => el.textContent);
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

it('puts contribution text before the recommendation only in flat full view', () => {
  const { container } = render(<PaperReview flat deep={deep} />);
  expect(container.textContent.indexOf(deep.digest.tldr)).toBeLessThan(container.textContent.indexOf('SKIM'));
});

it('gives exactly repeated overstated findings one qualified home without guessing paraphrases', () => {
  const claim = 'Synthetic exact claim.';
  const variant = 'Synthetic almost exact claim.';
  const { container } = render(<PaperReview flat deep={{
    digest: { tldr: 'Contribution.', key_findings: [claim, variant] },
    quality: { quality_band: 'uncertain', overstatements: [claim] },
  }} />);
  expect(container.textContent.split(claim).length - 1).toBe(1);
  expect(container.textContent).toContain(variant);
  expect(container.textContent).toContain('Overstated claims · model judgment · low confidence, verify');
});

it('keeps goal states explicit while folding repeated reasons into native keyboard evidence', () => {
  const { container } = render(<PaperReview flat deep={{ goal_summaries: [{
    goal: 'Goal', retrieval_state: 'hit', relevant: true, summary: 'Finding.', supporting_quotes: ['Quote.'],
  }] }} />);
  const reason = [...container.querySelectorAll('div')].find(el => el.textContent === 'Relevant to this goal');
  expect(reason.closest('details').querySelector(':scope > summary').textContent.trim()).toBe('evidence');
  expect(container.textContent).toContain('● addressed');
  const summary = reason.closest('details').querySelector('summary');
  summary.focus();
  expect(document.activeElement).toBe(summary);
  expect(reason.closest('details').open).toBe(false);
});

it('retains non-paper findings when scientific critique is suppressed', () => {
  const { container } = render(<PaperReview flat deep={{
    digest: { key_findings: ['Claim'] },
    quality: { basis: 'non_paper', grade: 'A', overstatements: ['Claim'] },
  }} />);
  expect(container.textContent).toContain('Claim');
  expect(container.textContent).not.toContain('Quality A');
  expect(container.textContent).not.toContain('Overstated claims');
});


it('factors only exact prefixes with complete readable suffixes and individual source links', () => {
  const prefix = 'The review reports';
  const flags = [`${prefix} uncertainty with human mitigation.`, `${prefix} missing reliability estimates.`, `${prefix} unvalidated novelty assessment.`];
  const section = { id: 'abstract', title: 'Abstract', page: 2 };
  const { container } = render(<PaperReview flat deep={{ quality: { quality_band: 'flag', red_flags: flags } }}
    sectionOverlay={{ red_flags: flags.map(text => ({ text, section, match: 'approx' })) }} />);
  const panel = container.querySelector('.border-rose-200');
  const group = panel.querySelector(':scope > ul > li');
  expect(group.firstChild.textContent).toBe(prefix);
  const suffixes = [...group.querySelectorAll(':scope > div > p')];
  expect(suffixes).toHaveLength(3);
  expect(suffixes.map(el => `${prefix} ${el.firstChild.textContent}`)).toEqual(flags);
  expect(suffixes.every(el => !el.closest('details'))).toBe(true);
  expect(panel.querySelectorAll('a')).toHaveLength(3);
  expect([...panel.querySelectorAll('details li')].map(el => el.textContent)).toEqual(flags);
});

it('never removes negation, internal context or different scopes by word matching', () => {
  const flags = ['No empirical validation is provided.', 'Empirical validation is provided.',
    'Training excludes trials with missing outcomes.', 'Evaluation includes trials with missing outcomes.'];
  const { container } = render(<PaperReview flat deep={{ quality: { red_flags: flags,
    rubric: { validation: 'no' } } }} />);
  const rows = [...container.querySelectorAll('.border-rose-200 > ul > li')];
  expect(rows.map(el => el.textContent)).toEqual(flags);
  expect(rows.every(el => !el.closest('details'))).toBe(true);
});

it('does not group warnings by criterion or source and leaves compact presentation unchanged', () => {
  const flags = ['Artifact license is unknown.', 'Artifacts were not independently reproduced.'];
  const section = { id: 'same', title: 'Methods' };
  const { container, rerender } = render(<PaperReview flat deep={{ quality: { red_flags: flags,
    rubric: { artifacts: 'no' } } }} sectionOverlay={{ red_flags: flags.map(text => ({ text, section, match: 'exact' })) }} />);
  expect(container.querySelectorAll('.border-rose-200 > ul > li')).toHaveLength(2);
  rerender(<PaperReview compact deep={deep} />);
  expect(container.textContent).not.toContain('Original wording');
});

it('groups identical goal associations under one heading while keeping different associations', () => {
  const goals = [{ goal: 'One', retrieval_state: 'hit', relevant: true, summary: 'First result. Second result.' },
    { goal: 'Two', retrieval_state: 'hit', relevant: true, summary: 'Third result.' }];
  const { container } = render(<PaperReview flat deep={{ goal_summaries: goals }} />);
  const labels = [...container.querySelectorAll('strong')].map(el => el.textContent).filter(text => text.startsWith('For:'));
  expect(labels).toEqual(['For: One', 'For: Two']);
  for (const text of ['First result.', 'Second result.', 'Third result.']) expect(container.textContent).toContain(text);
});

it.each(['validation', 'reliability', 'contamination'])('reconstructs literal %s frames without losing modifiers or suffixes', (term) => {
  const intro = 'No discussion of';
  const flags = [`${intro} ${term} or overlap with training data.`,
    `${intro} potential ${term} or overlap with pretraining corpora.`,
    `${intro} potential ${term} with LLM pretraining data.`];
  const section = { id: 'abstract', title: 'Abstract', page: 2 };
  const { container } = render(<PaperReview flat deep={{ quality: { red_flags: flags,
    rubric: { [term]: 'no' } } }}
    sectionOverlay={{ red_flags: flags.map(text => ({ text, section, match: 'exact' })) }} />);
  const panel = container.querySelector('.border-rose-200');
  const rows = [...panel.querySelectorAll(':scope > ul > li > div > p')].filter(row => row.querySelector('[data-frame-suffix]'));
  expect(rows).toHaveLength(3);
  expect(rows.map(row => [intro, row.querySelector('[data-frame-modifier]').textContent,
    term, row.querySelector('[data-frame-suffix]').textContent].filter(Boolean).join(' '))).toEqual(flags);
  expect(rows.every(row => !row.closest('details'))).toBe(true);
  expect(rows.every(row => row.querySelector('a'))).toBe(true);
  expect([...panel.querySelectorAll('details li')].map(row => row.textContent)).toEqual(flags);
});

it('does not factor a non-rubric term and never produces an empty prefix continuation', () => {
  const flags = ['No validation', 'No validation with independent data.', 'Validation is provided.'];
  const { container } = render(<PaperReview flat deep={{ quality: { red_flags: flags } }} />);
  expect([...container.querySelectorAll('.border-rose-200 > ul > li')].map(row => row.textContent)).toEqual(flags);
});

it('reports saved basis literally and never substitutes checklist counts for reviewed extent', () => {
  const { container } = render(<PaperReview flat deep={{ digest: { basis: 'full_text' },
    quality: { basis: 'abstract', coverage_met: 4, coverage_applicable: 7 } }} />);
  expect(container.textContent).toContain('Saved source basis — quality: abstract; digest: full_text. Reviewed extent not recorded.');
});
