// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

afterEach(cleanup);

vi.mock('./components/NavBar.jsx', () => ({ default: () => null }));
vi.mock('./pages/Today.jsx', () => ({ default: () => <p>Today destination</p> }));
vi.mock('./pages/Settings.jsx', () => ({ default: () => <p>Settings destination</p> }));
vi.mock('./pages/Library.jsx', () => ({ default: () => <p>Library destination</p> }));
vi.mock('./pages/Search.jsx', () => ({ default: () => <p>Search destination</p> }));
vi.mock('./pages/PaperReviewPage.jsx', () => ({ default: () => <p>Paper destination</p> }));
vi.mock('./pages/Ops.jsx', () => ({ default: () => <p>Ops destination</p> }));
vi.mock('./pages/SetupFlow.jsx', () => ({ default: () => <p>Setup destination</p> }));
vi.mock('./components/setup/SetupGate.jsx', () => ({ default: ({ children }) => children }));

import App from './App.jsx';

function CurrentLocation() {
  const { pathname, search } = useLocation();
  return <output data-testid="location">{pathname}{search}</output>;
}

describe('legacy route compatibility', () => {
  it('redirects Feed Review to Ops while preserving the pile filter and other query values', async () => {
    render(
      <MemoryRouter initialEntries={['/review?state=gate_rejected&from=bookmark']}>
        <CurrentLocation />
        <App />
      </MemoryRouter>,
    );

    await screen.findByText('Ops destination');
    await waitFor(() => expect(screen.getByTestId('location').textContent).toBe(
      '/ops?state=gate_rejected&from=bookmark&tab=review',
    ));
  });

  it('redirects the old annotation URL into batch mode without dropping its deep-link key', async () => {
    render(
      <MemoryRouter initialEntries={['/annotate?item_key=P1&from=bookmark']}>
        <CurrentLocation />
        <App />
      </MemoryRouter>,
    );

    await screen.findByText('Library destination');
    await waitFor(() => expect(screen.getAllByTestId('location').at(-1).textContent).toBe(
      '/library?item_key=P1&from=bookmark&mode=batch',
    ));
  });
});
