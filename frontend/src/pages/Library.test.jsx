// @vitest-environment jsdom
import { expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
vi.mock('./LibraryReadNext.jsx', () => ({ default: () => <p>Read-next queue</p> }));
vi.mock('./AnnotationVerdict.jsx', () => ({ default: () => <p>Batch verdict queue</p> }));

import Library from './Library.jsx';

function Query() {
  const location = useLocation();
  return <output data-testid="query">{location.search}</output>;
}

it('honors a batch deep link and preserves its paper key when switching modes', async () => {
  render(<MemoryRouter initialEntries={['/library?mode=batch&item_key=P1']}>
    <Query /><Library />
  </MemoryRouter>);

  expect(await screen.findByText('Batch verdict queue')).toBeTruthy();
  expect(screen.getByRole('tab', { name: 'Select & label' }).getAttribute('aria-selected')).toBe('true');
  fireEvent.click(screen.getByRole('tab', { name: 'Read next' }));
  expect(await screen.findByText('Read-next queue')).toBeTruthy();
  await waitFor(() => expect(screen.getByTestId('query').textContent).toBe('?item_key=P1'));
});
