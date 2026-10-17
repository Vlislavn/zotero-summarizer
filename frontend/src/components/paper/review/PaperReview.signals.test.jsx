// @vitest-environment jsdom
import React from 'react';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import PaperReview from './PaperReview.jsx';

afterEach(cleanup);

const reviewed = (digest = {}, quality = {}) => ({
  digest: { tldr: 'Synthetic concept.', novelty: 5, significance: 4, relevance: 'Supports synthetic project.',
    read_decision: 'skim', read_parts: ['Fig. 1', '§2.1'], writing_friction: 'high',
    writing_reasons: ['§2 uses the central construct before defining it.', 'Fig. 1 mixes two taxonomy axes.'],
    ...digest },
  quality: { quality_band: 'neutral', paper_type: 'position', coverage_standard: 'Argument quality', ...quality },
  goal_summaries: [{ goal: 'Synthetic project', retrieval_state: 'hit', relevant: true, abstained: false }],
  model_read_decision: 'skim', reading_policy_flags: [],
});

it.each([{}, { compact: true }, { flat: true }])('exposes separate signals and the cap in the shared review (%j)', (props) => {
  const deep = { ...reviewed(), model_read_decision: 'read',
    reading_policy_flags: ['writing_friction', 'strong_evidence_not_confirmed'] };
  const before = JSON.stringify(deep);
  render(<PaperReview deep={deep} {...props} />);

  const signals = screen.getByRole('group', { name: 'Idea, evidence and writing' });
  expect(signals.textContent).toContain('Idea: high');
  expect(signals.textContent).toContain('Evidence: Neutral');
  expect(signals.textContent).toContain('Writing: high friction');
  expect(signals.textContent).toContain('Idea preserved');
  expect(signals.textContent).toContain('Automatic policy capped');
  const disclosure = signals.querySelector('details');
  expect(disclosure.open).toBe(false);
  const summary = disclosure.querySelector('summary');
  summary.focus();
  expect(document.activeElement).toBe(summary);
  fireEvent.click(summary);
  expect(disclosure.open).toBe(true);
  expect(signals.textContent).toContain('Novelty 5/5');
  expect(signals.textContent).toContain('Significance 4/5');
  expect(signals.textContent).toContain('Position / perspective');
  expect(signals.textContent).toContain('Argument quality');
  for (const reason of deep.digest.writing_reasons) {
    expect(within(signals).getByText(reason)).toBeTruthy();
    expect(screen.getAllByText(reason)).toHaveLength(1);
  }
  expect(JSON.stringify(deep)).toBe(before);
});

it.each([{}, { compact: true }])('scopes readable summary and expanded label colors outside the full reader (%j)', (props) => {
  render(<PaperReview deep={reviewed()} {...props} />);
  const signals = screen.getByRole('group', { name: 'Idea, evidence and writing' });
  expect(signals.closest('.review-reading, .paper-reading-page')).toBeNull();
  expect(signals.classList.contains('[&_summary]:text-slate-600')).toBe(true);
  expect(signals.classList.contains('[&_dt]:text-slate-600')).toBe(true);
  const summary = signals.querySelector('summary');
  summary.focus();
  expect(document.activeElement).toBe(summary);
  fireEvent.click(summary);
  expect(summary.parentElement.open).toBe(true);
  expect([...signals.querySelectorAll('dt')].map(label => label.textContent)).toEqual([
    'Idea value', 'Scientific quality', 'Writing friction · high',
  ]);
});

it.each(['flag', 'highlight'])('keeps clear writing independent of evidence concerns (%s)', (band) => {
  render(<PaperReview compact deep={reviewed({ writing_friction: 'low', writing_reasons: [] },
    { quality_band: band, overstatements: ['The conclusion goes beyond its stated support.'] })} />);

  const signals = screen.getByRole('group', { name: 'Idea, evidence and writing' });
  expect(signals.textContent).toContain(`Evidence: ${band === 'flag' ? 'weak (Flag)' : 'Highlight'}`);
  expect(signals.textContent).toContain('Writing: low friction');
  const warning = within(signals).getByText(/Concept\/hypothesis source/);
  expect(warning.closest('details')).toBeNull();
  expect(warning.textContent).toContain('claims are not established convincingly');
  expect(signals.textContent).not.toContain('high writing friction');
});

it.each([
  ['abstract_only', 'Abstract-only digest'],
  ['full_text_not_confirmed', 'Digest source basis is unknown or unconfirmed'],
])('explains the server source-basis cap without inferring full text from quality (%s)', (flag, reason) => {
  render(<PaperReview deep={{ ...reviewed(), model_read_decision: 'read', reading_policy_flags: [flag] }} />);
  const signals = screen.getByRole('group', { name: 'Idea, evidence and writing' });
  expect(signals.textContent).toContain(reason);
  expect(signals.textContent).toContain('Automatic policy capped');
  if (flag === 'full_text_not_confirmed') expect(signals.textContent).not.toContain('Abstract-only digest');
});

it('keeps old cached writing unknown instead of substituting clarity or a missing quality result', () => {
  render(<PaperReview deep={{ digest: { read_decision: 'skim', clarity: 5, grade: 'A' },
    quality: null, goal_summaries: [] }} />);

  const signals = screen.getByRole('group', { name: 'Idea, evidence and writing' });
  expect(signals.textContent).toContain('Idea: not assessed');
  expect(signals.textContent).toContain('Evidence: not assessed');
  expect(signals.textContent).toContain('Writing: not assessed');
  expect(signals.textContent).toContain('Goal match unknown');
  expect(signals.textContent).toContain('Missing writing signal is unknown');
  expect(signals.textContent).not.toContain('low friction');
  expect(signals.textContent).not.toContain('Concept/hypothesis source');
});

it('does not turn uncertain scientific quality into weak evidence or difficult prose', () => {
  render(<PaperReview deep={reviewed({ writing_friction: 'moderate', writing_reasons: ['§3 leaves an acronym unexplained.'] },
    { quality_band: 'uncertain' })} />);

  const signals = screen.getByRole('group', { name: 'Idea, evidence and writing' });
  expect(signals.textContent).toContain('Evidence: Uncertain');
  expect(signals.textContent).toContain('Writing: moderate friction');
  expect(signals.textContent).not.toContain('claims are not established');
});

it('does not grade non-paper science or issue a targeted-skim instruction for a saved skip', () => {
  render(<PaperReview deep={reviewed({ read_decision: 'skip' },
    { basis: 'non_paper', quality_band: 'flag', overstatements: ['Legacy concern.'] })} />);

  const signals = screen.getByRole('group', { name: 'Idea, evidence and writing' });
  expect(signals.textContent).toContain('Evidence: not applicable');
  expect(signals.textContent).toContain('Writing: high friction');
  expect(signals.textContent).not.toContain('claims are not established');
  expect(signals.textContent).not.toContain('limits automatic advice to a targeted skim');
});

it('keeps the position-paper assessment free of invented empirical failures', () => {
  render(<PaperReview flat deep={reviewed({}, { quality_band: 'flag',
    rubric: { thesis: 'yes', evidence_cited: 'no', counterarguments: 'na', scope_match: 'yes' } })} />);

  expect(screen.getAllByText('Supports premises with cited evidence').length).toBeGreaterThan(0);
  expect(screen.queryByText('Ablations isolate what drives the result')).toBeNull();
  expect(screen.queryByText('Validated on independent / held-out data')).toBeNull();
  expect(screen.queryByText('Reports uncertainty (CIs, error bars, seeds)')).toBeNull();
});

it.each([undefined, true])('does not turn absent or withheld goal assessment into a confirmed match (%s)', (abstained) => {
  const deep = { ...reviewed(), goal_summaries: [{ retrieval_state: 'hit', relevant: true, abstained }] };
  render(<PaperReview deep={deep} />);
  const signals = screen.getByRole('group', { name: 'Idea, evidence and writing' });
  expect(signals.textContent).toContain('Goal match unknown');
  expect(signals.textContent).not.toContain('Supported goal match recorded');
});

it('renders stored writing reasons as text, never as markup', () => {
  const reason = '<img src=x onerror=alert(1)> is a literal synthetic source example.';
  render(<PaperReview deep={reviewed({ writing_reasons: [reason] })} />);
  const signals = screen.getByRole('group', { name: 'Idea, evidence and writing' });
  expect(within(signals).getByText(reason)).toBeTruthy();
  expect(signals.querySelector('img')).toBeNull();
});

it('shows real goal misses without treating retrieval failure as a miss', () => {
  const deep = reviewed({ novelty: 1, significance: 2, writing_friction: 'low', writing_reasons: [] });
  const { rerender } = render(<PaperReview deep={{ ...deep,
    goal_summaries: [{ retrieval_state: 'miss', relevant: false }] }} />);
  let signals = screen.getByRole('group', { name: 'Idea, evidence and writing' });
  expect(signals.textContent).toContain('Idea: low');
  expect(signals.textContent).toContain('No goal match recorded');
  rerender(<PaperReview deep={{ ...deep, goal_summaries: [{ retrieval_state: 'not_retrieved', relevant: false }] }} />);
  signals = screen.getByRole('group', { name: 'Idea, evidence and writing' });
  expect(signals.textContent).toContain('Goal match unknown');
  expect(signals.textContent).not.toContain('No goal match recorded');
});
