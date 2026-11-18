// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { expect, it, vi } from 'vitest';

vi.mock('./Review.jsx', () => ({ default: () => <p>Feed Review tools</p> }));
vi.mock('./Triage.jsx', () => ({ default: () => <p>Triage job tools</p> }));
vi.mock('./Pending.jsx', () => ({ default: () => <p>Pending change tools</p> }));
vi.mock('../components/AdminSection.jsx', () => ({ default: () => <p>Admin controls</p> }));
vi.mock('../components/ModelCard.jsx', () => ({ default: () => <p>Model status</p> }));
vi.mock('../components/FeedbackAnalysisCard.jsx', () => ({ default: () => <p>Your decisions</p> }));

import Ops from './Ops.jsx';

function CurrentSearch() {
  const location = useLocation();
  return <output data-testid="current-search">{location.search}</output>;
}

it('provides the System section h2 before its reusable cards', () => {
  const view = render(<MemoryRouter initialEntries={['/ops?tab=system']}><Ops /></MemoryRouter>);
  try {
    expect(screen.getByRole('heading', { name: 'System', level: 2 })).toBeTruthy();
  } finally {
    view.unmount();
  }
});

it('opens the deep-linked Ops tab and retains unrelated query state when switching tabs', async () => {
  render(
    <MemoryRouter initialEntries={['/ops?tab=triage&job=J42']}>
      <CurrentSearch />
      <Ops />
    </MemoryRouter>,
  );

  expect(await screen.findByText('Triage job tools')).toBeTruthy();
  expect(screen.getByRole('tab', { name: 'Triage jobs' }).getAttribute('aria-selected')).toBe('true');
  fireEvent.click(screen.getByRole('tab', { name: 'Pending changes' }));

  expect(await screen.findByText('Pending change tools')).toBeTruthy();
  await waitFor(() => expect(screen.getByTestId('current-search').textContent).toBe('?tab=pending&job=J42'));
});

it('gives /ops exactly one h1 (the page title)', () => {
  cleanup(); // the previous test leaves its tree mounted
  for (const url of ['/ops', '/ops?tab=system']) {
    const view = render(<MemoryRouter initialEntries={[url]}><Ops /></MemoryRouter>);
    try {
      expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1);
    } finally {
      view.unmount();
    }
  }
  render(<MemoryRouter initialEntries={['/ops']}><Ops /></MemoryRouter>);
  expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('Ops');
});
