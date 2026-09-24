// @vitest-environment jsdom
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { expect, it, vi } from 'vitest';

vi.mock('./Review.jsx', () => ({ default: () => <p>Feed Review tools</p> }));
vi.mock('./Triage.jsx', () => ({ default: () => <p>Triage job tools</p> }));
vi.mock('./Pending.jsx', () => ({ default: () => <p>Pending change tools</p> }));
vi.mock('../components/AdminSection.jsx', () => ({ default: () => <p>Admin controls</p> }));
vi.mock('../components/ModelCard.jsx', () => ({ default: () => <p>Model status</p> }));

import Ops from './Ops.jsx';

function CurrentSearch() {
  const location = useLocation();
  return <output data-testid="current-search">{location.search}</output>;
}

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
