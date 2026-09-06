// @vitest-environment jsdom
import { afterEach, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import ProvenanceBreakdown from './ProvenanceBreakdown.jsx';

afterEach(cleanup);

it('explains an explicit label without inventing an additive derivation', () => {
  render(<ProvenanceBreakdown provenance={{
    derived_priority: 'must_read', derived_score: 5, persisted_priority: 'dont_read',
    is_manual_override: true,
    short_circuits: { explicit_label: 'must_read', in_trash_override: false, hard_veto_emojis: [] },
  }} />);
  expect(screen.getByText(/label:must_read/)).toBeTruthy();
  expect(screen.getByText(/takes precedence over trash/)).toBeTruthy();
  expect(screen.queryByRole('table')).toBeNull();
  expect(screen.queryByText(/Bins:/)).toBeNull();
  expect(screen.getByText('manual override')).toBeTruthy();
  expect(screen.getByText(/persisted:/)).toBeTruthy();
});

it('retains the additive table for an engagement-derived label', () => {
  render(<ProvenanceBreakdown provenance={{ derived_priority: 'could_read', derived_score: 3,
    short_circuits: { explicit_label: null }, additive_scoring: { baseline: 3 } }} />);
  expect(screen.getByRole('table')).toBeTruthy();
  expect(screen.getByText('baseline')).toBeTruthy();
  expect(screen.queryByText(/label:must_read/)).toBeNull();
});
