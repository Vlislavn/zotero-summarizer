// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import PaperCard from './PaperCard.jsx';
import palette from '../../../tailwind.config.js';

afterEach(cleanup);

function evidence(overrides = {}) {
  return {
    mode: 'control', stored: { composite_recorded: true, reading_priority: 'could_read' },
    review: { join: 'matched', join_basis: 'stable_feed', quality_origin: 'current_entry',
      needs_pdf: false, digest_usable: true },
    rank: { basis: 'relevance_goal_citation', key: 0.62 },
    selection: { requested_role: 'model', basis: 'model_adjusted', key: 0.67,
      quality_bonus: 0.05, bonus_policy: 'grade', band_restricted: true },
    ...overrides,
  };
}

function card(overrides = {}) {
  return { item_id: 1, title: 'Synthetic paper', composite_score: 3, role: 'model',
    quality: { grade: 'A' }, review_ready: true, selection_evidence: evidence(), ...overrides };
}

function show(paper, selectionPolicy = 'control', onToggleSelect = () => {}) {
  return render(<MemoryRouter><PaperCard paper={paper} selectionPolicy={selectionPolicy}
    selected={false} onToggleSelect={onToggleSelect} /></MemoryRouter>);
}

function luminance(hex) {
  const rgb = [1, 3, 5].map((offset) => parseInt(hex.slice(offset, offset + 2), 16) / 255);
  const linear = rgb.map((value) => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4);
  return linear[0] * 0.2126 + linear[1] * 0.7152 + linear[2] * 0.0722;
}

function paletteContrast(foreground, background) {
  const values = [luminance(foreground), luminance(background)];
  return (Math.max(...values) + 0.05) / (Math.min(...values) + 0.05);
}

function utilityColor(node, prefix) {
  const colors = palette.theme.extend.colors;
  const token = [...node.classList].find((name) => name.startsWith(prefix)
    && colors[name.split('-')[1]]);
  const [, name, shade] = token.split('-');
  return shade ? colors[name][shade] : colors[name];
}

it('uses a readable contrast token on the selection disclosure', () => {
  show(card());
  const summary = screen.getByText('Why selected').closest('summary');
  expect(summary.closest('details').parentElement.className).toContain('[&_summary]:text-slate-600');
  const colors = palette.theme.extend.colors;
  expect(paletteContrast(colors.white, colors.slate[600])).toBeGreaterThanOrEqual(4.5);
});

it('keeps the actual moderate relevance pill readable using the shared palette', () => {
  show(card());
  const pill = screen.getByText('Relevance: moderate');
  expect(paletteContrast(utilityColor(pill, 'text-'), utilityColor(pill, 'bg-'))).toBeGreaterThanOrEqual(4.5);
});

it('keeps the unscored pill readable on its actual neutral background', () => {
  show(card({ composite_score: null }));
  const pill = screen.getByText('Relevance: unscored');
  expect(paletteContrast(utilityColor(pill, 'text-'), utilityColor(pill, 'bg-'))).toBeGreaterThanOrEqual(4.5);
});

it('keeps every observed-value label readable without changing the palette', () => {
  const { container } = show(card());
  const colors = palette.theme.extend.colors;
  for (const label of container.querySelectorAll('dt')) {
    expect(paletteContrast(utilityColor(label, 'text-'), colors.white)).toBeGreaterThanOrEqual(4.5);
  }
});

it.each([
  [0, 'weak'], [1.9999, 'weak'], [2, 'moderate'], [2.8, 'moderate'], [3.4999, 'moderate'],
  [3.5, 'strong'], [4.4999, 'strong'], [4.5, 'top pick'],
])('labels the unchanged composite boundary %s as relevance %s', (score, word) => {
  show(card({ composite_score: score }));
  expect(screen.getByText(`Relevance: ${word}`)).toBeTruthy();
  expect(screen.queryByText(/^fair$/i)).toBeNull();
});

it.each([undefined, null, NaN, Infinity, -Infinity, '3'])('leaves missing/invalid score %s unscored', (score) => {
  show(card({ composite_score: score }));
  expect(screen.getByText('Relevance: unscored')).toBeTruthy();
});

it('does not present the backend zero fallback as an observed weak score', () => {
  show(card({ composite_score: 0, selection_evidence: evidence({
    stored: { composite_recorded: false, reading_priority: 'must_read' },
  }) }));
  expect(screen.getByText('Relevance: unscored')).toBeTruthy();
  expect(screen.getByText('Composite not recorded')).toBeTruthy();
  expect(screen.getByText('must_read')).toBeTruthy();
});

it('distinguishes same-relevance current evidence grades without implying scientific validation', () => {
  show(card());
  show(card({ item_id: 2, title: 'Second paper', quality: { grade: 'D' } }));
  expect(screen.getAllByText('Relevance: moderate')).toHaveLength(2);
  expect(screen.getByText('Evidence: grade A (AI review)')).toBeTruthy();
  expect(screen.getByText('Evidence: grade D (AI review)')).toBeTruthy();
  expect(screen.getAllByText(/not independent scientific validation/)).toHaveLength(2);
});

it.each([
  [{ grade: '' }], [{}], [{ grade: 'private-unknown-grade' }],
])('keeps missing/unknown current quality distinct from a neutral grade: %s', (quality) => {
  show(card({ quality }));
  expect(screen.getByText('Review available · evidence grade unknown')).toBeTruthy();
  expect(screen.queryByText(/Evidence: grade/)).toBeNull();
});

it('labels a legacy grade as unconfirmed when the current review join misses', () => {
  show(card({ review_ready: false, selection_evidence: evidence({ review: {
    join: 'unavailable', join_basis: null, quality_origin: 'legacy_row',
    needs_pdf: null, digest_usable: null,
  } }) }));
  expect(screen.getByText('Evidence: legacy grade A · current review unconfirmed')).toBeTruthy();
  expect(screen.queryByText('Evidence: grade A (AI review)')).toBeNull();
  expect(screen.getByText(/does not establish whether a review is absent, stale or failed/)).toBeTruthy();
});

it('leaves failed, stale and never-reviewed indistinguishable when only a current join miss is observed', () => {
  show(card({ quality: {}, review_ready: false, selection_evidence: evidence({ review: {
    join: 'unavailable', join_basis: null, quality_origin: 'none',
    needs_pdf: null, digest_usable: null,
  } }) }));
  expect(screen.getByText('Review evidence: unknown')).toBeTruthy();
  expect(screen.queryByText(/^Review: (failed|stale|not reviewed)$/)).toBeNull();
});

it('shows missing full text separately, never as poor science', () => {
  show(card({ review_ready: false, selection_evidence: evidence({ review: {
    join: 'matched', join_basis: 'stable_feed', quality_origin: 'current_entry',
    needs_pdf: true, digest_usable: false,
  } }) }));
  expect(screen.getByText('Review: full text unavailable')).toBeTruthy();
  expect(screen.queryByText('Evidence: grade A (AI review)')).toBeNull();
});

it('does not call an unusable joined digest a failed scientific review', () => {
  show(card({ review_ready: false, selection_evidence: evidence({ review: {
    join: 'matched', join_basis: 'stable_feed', quality_origin: 'current_entry',
    needs_pdf: false, digest_usable: false,
  } }) }));
  expect(screen.getByText('Review: not ready')).toBeTruthy();
  expect(screen.getByText(
    'A current entry matched, but its digest is not usable. Failure and staleness are not recorded here.',
  )).toBeTruthy();
});

it('does not upgrade old payload quality to a current review', () => {
  show(card({ selection_evidence: undefined }));
  expect(screen.getByText('Review available · evidence origin unrecorded')).toBeTruthy();
  expect(screen.queryByText('Evidence: grade A (AI review)')).toBeNull();
  expect(screen.getByText('Selection details were not recorded for this card.')).toBeTruthy();
});

it('keeps a nonidentity stored priority separate from the composite without inventing a calibrator', () => {
  show(card({ selection_evidence: evidence({ stored: {
    composite_recorded: true, reading_priority: 'must_read',
  } }), why: ['On-topic for you'] }), 'quality_first');
  const details = screen.getByText('Why selected').closest('details');
  expect(details.open).toBe(false);
  fireEvent.click(screen.getByText('Why selected'));
  expect(within(details).getByText('Quality-first')).toBeTruthy();
  expect(within(details).getByText('On-topic for you')).toBeTruthy();
  expect(within(details).getByText('0.62')).toBeTruthy();
  expect(within(details).getByText('0.67')).toBeTruthy();
  expect(within(details).getByText('must_read')).toBeTruthy();
  expect(within(details).getByText(/not raw model predictions or calibrated probabilities/)).toBeTruthy();
  expect(screen.getByText('Relevance: moderate')).toBeTruthy();
});

it.each([
  ['surprise', 'surprise', /Exploration: an unexpected pick/],
  ['diversity', 'cohort', /Exploration: outside your usual saved-paper mix/],
  ['model_fallback', 'model_adjusted', /Main recommendation filling an unfilled diversity slot/],
])('explains the observed %s role without treating exploration as quality', (role, basis, description) => {
  show(card({ role, selection_evidence: evidence({ selection: {
    requested_role: role === 'model_fallback' ? 'diversity' : role, basis, key: 0.41,
    quality_bonus: null, bonus_policy: 'not_used', band_restricted: false,
  } }) }));
  expect(screen.getByText(description)).toBeTruthy();
});

it('retains observed zero keys and leaves missing keys unknown without rebuilding them from scores', () => {
  show(card({ selection_evidence: evidence({ rank: { basis: 'relevance_goal_citation', key: null },
    selection: { requested_role: 'model', basis: 'model_adjusted', key: 0,
      quality_bonus: 0, bonus_policy: 'grade', band_restricted: true },
  }) }));
  const details = screen.getByText('Why selected').closest('details');
  const valueFor = (label) => within(details).getByText(`${label}:`).nextElementSibling.textContent;
  expect(valueFor('Cohort key')).toBe('Unknown');
  expect(valueFor('Picker key')).toBe('0');
  expect(valueFor('Quality adjustment')).toBe('0');
  expect(screen.getByText('Evidence: grade A (AI review)')).toBeTruthy();
});

it.each([
  ['interleave', evidence()],
  ['control', { mode: 'interleave', basis: 'unavailable_blinded' }],
  [undefined, { basis: 'unavailable_blinded' }],
])('withholds all new diagnostics when either boundary says blinded: %s', (policy, observed) => {
  const { container } = show(card({ role: 'model_fallback', quality: { grade: 'D' },
    why: ['Private arm hint'], selection_evidence: { ...observed, private_c: 'PRIVATE-SENTINEL' },
  }), policy);
  expect(screen.getByText('Review evidence: unavailable (blinded selection)')).toBeTruthy();
  const details = screen.getByText('Why selected').closest('details');
  expect(within(details).getByText(/withheld to keep this selection blinded/)).toBeTruthy();
  for (const secret of ['PRIVATE-SENTINEL', 'Private arm hint', 'grade D', '0.62', '0.67', 'model_fallback']) {
    expect(container.textContent).not.toContain(secret);
  }
});

it('rendering and disclosure leave scores, stored labels and selection intents untouched', () => {
  const paper = card();
  const before = JSON.stringify(paper);
  const onToggle = vi.fn();
  show(paper, 'control', onToggle);
  fireEvent.click(screen.getByText('Why selected'));
  expect(onToggle).not.toHaveBeenCalled();
  expect(JSON.stringify(paper)).toBe(before);
  fireEvent.click(screen.getByRole('checkbox'));
  expect(onToggle).toHaveBeenCalledTimes(1);
  expect(onToggle).toHaveBeenCalledWith(1);
  expect(JSON.stringify(paper)).toBe(before);
});
