// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
vi.mock('./LibraryReadNext.jsx', () => ({ default: () => <p>Read-next queue</p> }));
vi.mock('./AnnotationVerdict.jsx', () => ({ default: () => <p>Batch verdict queue</p> }));

import Library from './Library.jsx';

afterEach(cleanup);

function Query() {
  const location = useLocation();
  return <output data-testid="query">{location.search}</output>;
}

it('honors a batch deep link and preserves its paper key when switching modes', async () => {
  render(<MemoryRouter initialEntries={['/library?mode=batch&item_key=P1']}>
    <Query /><Library />
  </MemoryRouter>);

  expect(await screen.findByText('Batch verdict queue')).toBeTruthy();
  expect(screen.getByRole('tab', { name: 'My verdicts' }).getAttribute('aria-selected')).toBe('true');
  fireEvent.click(screen.getByRole('tab', { name: 'Read next' }));
  expect(await screen.findByText('Read-next queue')).toBeTruthy();
  await waitFor(() => expect(screen.getByTestId('query').textContent).toBe('?item_key=P1'));
});

it('has one h1 and puts the mode tabs in the title row', () => {
  render(<MemoryRouter initialEntries={['/library']}><Library /></MemoryRouter>);

  expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1);
  const row = screen.getByRole('heading', { level: 1, name: 'Library' }).parentElement;
  expect(within(row).getByRole('tablist', { name: 'Library modes' })).toBeTruthy();
  expect(within(row).getAllByRole('tab').map((t) => t.textContent)).toEqual(['Read next', 'My verdicts']);
});
