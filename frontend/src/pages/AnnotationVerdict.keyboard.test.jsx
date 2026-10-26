// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import AnnotationVerdict from './AnnotationVerdict.jsx';
import { fetchProvenanceList, fetchReviewDetail, fetchEffectiveLabels, submitVerdict } from '../api/goldenApi.js';

vi.mock('../api/goldenApi.js', () => ({
  fetchProvenanceList: vi.fn(), fetchReviewDetail: vi.fn(), fetchEffectiveLabels: vi.fn(),
  fetchBorderSuggestions: vi.fn(), submitVerdict: vi.fn(), deleteVerdict: vi.fn(),
}));
vi.mock('../api/libraryApi.js', () => ({
  fetchCollections: async () => ({ items: [] }), fetchTags: async () => ({ items: [] }),
}));
vi.mock('../components/paper/PaperDetailView/index.jsx', () => ({ default: () => null }));

const a = { item_key: 'KEY_A', title: 'Loaded Alpha', verdict: null };
const b = { item_key: 'KEY_B', title: 'Delayed Beta',
  verdict: { user_priority: 'should_read', comment: 'Keep this saved rationale' } };
const clients = [];
beforeEach(() => {
  fetchProvenanceList.mockResolvedValue({ items: [
    { ...a, derived_score: 2 }, { ...b, derived_score: 1 },
  ], total_matched: 2 });
  fetchEffectiveLabels.mockResolvedValue({ items: [] });
  submitVerdict.mockResolvedValue({ id: 1 });
});
afterEach(() => {
  cleanup();
  clients.splice(0).forEach(client => client.clear());
  vi.restoreAllMocks();
  vi.resetAllMocks();
});

function mount(client = new QueryClient({ defaultOptions: { queries: { retry: false } } })) {
  clients.push(client);
  render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/library?mode=batch']}>
    <AnnotationVerdict />
  </MemoryRouter></QueryClientProvider>);
  return client;
}

function press(key) {
  document.activeElement?.blur();
  expect(document.activeElement).toBe(document.body);
  fireEvent.keyDown(document.body, { key });
}

it('blocks 1–4 for an uncached selection, keeps j/k and preserves its rationale after loading', async () => {
  let finish;
  const delayed = new Promise(resolve => { finish = resolve; });
  fetchReviewDetail.mockImplementation(key => key === a.item_key ? Promise.resolve(a) : delayed);
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
  mount();
  await screen.findByRole('heading', { name: a.title });

  press('j');
  await screen.findByText('Loading paper detail…');
  await act(async () => { for (const key of ['1', '2', '3', '4']) press(key); });
  expect(submitVerdict).not.toHaveBeenCalled();
  expect(confirm).not.toHaveBeenCalled();
  press('k');
  await screen.findByRole('heading', { name: a.title });
  press('j');
  await screen.findByText('Loading paper detail…');
  await act(async () => { finish(b); });
  await screen.findByRole('heading', { name: b.title });

  press('1');
  expect(confirm).toHaveBeenCalledOnce();
  expect(submitVerdict).not.toHaveBeenCalled();
  confirm.mockReturnValue(true);
  press('3');
  await waitFor(() => expect(submitVerdict.mock.calls.map(([payload]) => payload)).toEqual([{
    item_key: b.item_key, user_priority: 'could_read', comment: 'Keep this saved rationale',
  }]));
});

it.each([false, true])('blocks keys after failed selected-detail reads with retained data=%s', async retained => {
  fetchReviewDetail.mockImplementation(key => key === a.item_key
    ? Promise.resolve(a) : Promise.reject(new Error('Detail read failed')));
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  if (retained) client.setQueryData(['review-detail', b.item_key], b);
  mount(client);
  await screen.findByRole('heading', { name: a.title });
  press('j');
  await waitFor(() => expect(client.getQueryState(['review-detail', b.item_key]).status).toBe('error'));
  await screen.findByRole('alert');
  await act(async () => { for (const key of ['1', '2', '3', '4']) press(key); });
  expect(submitVerdict).not.toHaveBeenCalled();
  expect(confirm).not.toHaveBeenCalled();
  press('k');
  await screen.findByRole('heading', { name: a.title });
});

it('refuses foreign detail even when the selected query is marked successful', async () => {
  fetchReviewDetail.mockResolvedValue(a);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryDefaults(['review-detail', b.item_key], { staleTime: Infinity });
  client.setQueryData(['review-detail', b.item_key], a);
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
  mount(client);
  await screen.findByRole('heading', { name: a.title });
  press('j');
  await waitFor(() => expect(client.getQueryState(['review-detail', b.item_key]).status).toBe('success'));
  expect(screen.getByRole('button', { name: /Delayed Beta/ }).className).toContain('ring-1');
  await act(async () => { for (const key of ['1', '2', '3', '4']) press(key); });
  expect(submitVerdict).not.toHaveBeenCalled();
  expect(confirm).not.toHaveBeenCalled();
});

it.each([null, b.verdict])('keeps loaded new/same-priority shortcuts available: %j', async verdict => {
  const detail = { ...b, verdict };
  fetchReviewDetail.mockImplementation(key => Promise.resolve(key === a.item_key ? a : detail));
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
  mount();
  await screen.findByRole('heading', { name: a.title });
  press('j');
  await screen.findByRole('heading', { name: b.title });
  press('2');
  await waitFor(() => expect(submitVerdict.mock.calls.map(([payload]) => payload)).toEqual([{
    item_key: b.item_key, user_priority: 'should_read', comment: verdict?.comment ?? '',
  }]));
  expect(confirm).not.toHaveBeenCalled();
});
