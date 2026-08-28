// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
const navigate = vi.hoisted(() => vi.fn());

vi.mock('react-router-dom', async (original) => ({
  ...await original(), useNavigate: () => navigate,
}));

vi.mock('../settings/DeploymentCard.jsx', () => ({
  DoctorChecklist: () => <button>Verify setup</button>,
}));

import StepDone from './StepDone.jsx';
beforeEach(() => {
  vi.clearAllMocks();
  const values = new Map();
  vi.stubGlobal('localStorage', {
    getItem: (key) => values.get(key) || null,
    setItem: (key, value) => values.set(key, String(value)),
    clear: () => values.clear(),
  });
});

afterEach(() => cleanup());

it('finishes immediately and leaves expensive verification explicit', () => {
  render(<StepDone />);

  expect(screen.getByRole('heading', { name: 'Setup saved' })).toBeTruthy();
  expect(screen.getByText(/verification is optional/i)).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Verify setup' })).toBeTruthy();

  fireEvent.click(screen.getByRole('button', { name: 'Open Today' }));
  expect(window.localStorage.getItem('zs:setupDismissed')).toBe('1');
  expect(navigate).toHaveBeenCalledWith('/today');
});
