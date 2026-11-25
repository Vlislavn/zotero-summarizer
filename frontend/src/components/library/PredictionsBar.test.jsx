// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';

import PredictionsBar from './PredictionsBar.jsx';

afterEach(cleanup);

it('reports missing browser support as setup work, not authentication', () => {
  render(<PredictionsBar
    fleetStatus={{
      status: 'done_empty', completed: 2, proposed: 0,
      browser_extra_unavailable: 2, needs_library_login: 0,
    }}
    onRun={vi.fn()}
    onStop={vi.fn()}
  />);
  expect(screen.getByText(/browser support is not installed/i)).toBeTruthy();
  expect(screen.queryByText(/sign in to fetch/i)).toBeNull();
});

it('shows no run button when there is nothing to review, but keeps the state line', () => {
  render(<PredictionsBar fleetStatus={null} onRun={vi.fn()} onStop={vi.fn()} coolCount={0} proposedCount={0} />);
  expect(screen.queryByRole('button')).toBeNull();
  expect(screen.getByRole('status').textContent).toBe('No undecided top picks.');
});

it('offers the run button, with its count, while top picks are undecided', () => {
  render(<PredictionsBar fleetStatus={null} onRun={vi.fn()} onStop={vi.fn()} coolCount={3} />);
  expect(screen.getByRole('button', { name: 'Deep-review top picks (3)' })).toBeTruthy();
});
