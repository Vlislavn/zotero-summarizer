// @vitest-environment jsdom
import React from 'react';
import { cleanup, render, within } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import PaperReview from './PaperReview.jsx';

afterEach(cleanup);

it('shows the policy reason for a capped SKIM with its concrete targets', () => {
  const target = '§3 Experimental design';
  const policyReason = 'Strong scientific support was not confirmed.';
  const { getByRole, getByText } = render(<PaperReview deep={{
    digest: {
      read_decision: 'skim', read_why: 'Full reading threshold not met; skim targeted parts.',
      read_parts: [target], original_value: 'Inspect the primary comparison.',
      novelty: 5, significance: 4, writing_friction: 'low',
    },
    quality: { basis: 'full_text', quality_band: 'neutral', missing_critical: ['sample representativeness'] },
    model_read_decision: 'read',
    reading_policy_flags: ['weak_evidence', 'strong_evidence_not_confirmed'],
  }} />);

  expect(getByText('Why SKIM')).toBeTruthy();
  expect(getByText(policyReason).closest('details')).toBeNull();
  expect(getByRole('region', { name: 'Reading plan' }).textContent).toContain(target);
  expect(within(getByRole('region', { name: 'Reading plan' })).getByText(target)).toBeTruthy();
  expect(getByText(/Concept\/hypothesis source/).closest('details')).toBeNull();
});

it('uses the shared weak-evidence flag for missing critical support', () => {
  const { getByText } = render(<PaperReview deep={{
    digest: {
      read_decision: 'skim', read_why: 'Inspect this claim before relying on it.',
      read_parts: ['§4 Results'], novelty: 5, significance: 4,
    },
    quality: { basis: 'full_text', quality_band: 'neutral', missing_critical: ['sample representativeness'] },
    model_read_decision: 'skim',
    reading_policy_flags: ['weak_evidence'],
  }} />);

  expect(getByText(/Concept\/hypothesis source/).closest('details')).toBeNull();
});
