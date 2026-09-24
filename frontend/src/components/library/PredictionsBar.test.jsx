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
