import { expect, it } from 'vitest';
import { decisiveRows, rubricLabel } from './briefModel.js';

it.each([
  ['thesis', 'States a clear thesis / position'],
  ['evidence_cited', 'Supports premises with cited evidence'],
  ['scope_match', 'Matches claim scope to the evidence offered'],
])('labels the supplied schema identifier %s without changing unknown-key fallback', (key, label) => {
  expect(rubricLabel(key)).toBe(label);
  expect(rubricLabel('future_criterion')).toBe('future criterion');
});

it.each(['flag', 'neutral', 'highlight', 'uncertain'])('summarizes only supplied position-paper criteria (%s)', (band) => {
  const rubric = { thesis: 'yes', evidence_cited: 'no', counterarguments: 'na', scope_match: 'yes' };

  const result = decisiveRows(rubric, band);

  const labels = result.rows.map(row => row.label);
  expect(labels.length).toBeGreaterThan(0);
  expect(labels.every(label => ['States a clear thesis / position', 'Supports premises with cited evidence',
    'Matches claim scope to the evidence offered'].includes(label))).toBe(true);
  expect(result.rows.find(row => row.label === 'Supports premises with cited evidence')?.ok)
    .toBe(band === 'highlight' ? undefined : false);
  if (band === 'highlight') expect(result.caption).toBe('2/3 applicable checks met');
});

it.each([
  { search_strategy: 'no', rob_assessment: 'yes' },
  { assumptions: 'yes', proofs: 'no' },
  { consent: 'no', patient_context: 'na' },
])('uses actual checklist gaps for reviews, theory and case reports (%j)', (rubric) => {
  const result = decisiveRows(rubric, 'flag');
  expect(result.rows).toEqual(Object.entries(rubric).filter(([, value]) => value === 'no')
    .map(([key]) => ({ ok: false, label: key.replace(/_/g, ' ') })));
});

it.each(['flag', 'highlight', 'uncertain', 'neutral'])('does not turn an absent rubric or N/A into failed empirical criteria (%s)', (band) => {
  expect(decisiveRows({}, band).rows).toEqual([]);
  expect(decisiveRows({ external_validation: 'na', ablation: 'na' }, band).rows).toEqual([]);
});

it('preserves legacy case normalization without manufacturing absent criteria', () => {
  expect(decisiveRows({ thesis: 'YES', evidence_cited: 'NO' }, 'flag').rows).toEqual([
    { ok: false, label: 'Supports premises with cited evidence' },
  ]);
});

it('keeps supplied empirical failures and excludes absent external-validation claims', () => {
  const result = decisiveRows({ ablation: 'no', uncertainty: 'yes', baselines: 'no' }, 'flag');
  expect(result.rows).toEqual([
    { ok: false, label: 'Ablations isolate what drives the result' },
    { ok: false, label: 'Compared against fair, current baselines' },
  ]);
});
