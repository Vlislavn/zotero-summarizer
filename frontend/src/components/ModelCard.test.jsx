// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fetchModelCard } from '../api/settingsApi.js';
import ModelCard from './ModelCard.jsx';

vi.mock('../api/settingsApi.js', () => ({ fetchModelCard: vi.fn() }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });

function show(model) {
  fetchModelCard.mockResolvedValue({ model });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><ModelCard /></QueryClientProvider>);
}

it('describes a missing loaded gate without claiming no artifact exists', async () => {
  show(null);
  expect(await screen.findByText(/No classifier is loaded/)).toBeTruthy();
  expect(screen.getByText(/restart to load a saved model/)).toBeTruthy();
  expect(screen.queryByText(/No trained model on disk/)).toBeNull();
});

it('renders the four-field current-gate contract', async () => {
  show({ classifier_name: 'lightgbm', n_train: 1171,
    trained_at: '2026-05-15T22:15:20Z', oof_spearman_verified: 0.14 });
  expect(await screen.findByText('lightgbm')).toBeTruthy();
  expect(screen.getByText('1171')).toBeTruthy();
  expect(screen.getByText('2026-05-15T22:15:20Z')).toBeTruthy();
  expect(screen.getByText('0.140')).toBeTruthy();
});
