// @vitest-environment jsdom
import 'fake-indexeddb/auto';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider, onlineManager } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import AnnotationVerdict from './AnnotationVerdict.jsx';
import { cacheResponse, pendingMutations, publishStatus, savePapers } from '../offlineStore.js';
const clients = [];
afterEach(() => { cleanup(); clients.forEach(client => client.clear()); onlineManager.setOnline(true); vi.restoreAllMocks(); });
it.each(['set', 'delete'])('persists an explicit batch %s while React Query considers the browser offline', async (operation) => {
  const detail = { item_key: `BATCH_OFFLINE_${operation}`, title: 'Batch offline fixture', tags: [], collections: [], has_pdf: false,
    verdict: operation === 'delete' ? { user_priority: 'should_read', comment: '' } : null };
  await savePapers([{ item_key: detail.item_key, revisions: {} }]);
  await cacheResponse(`review:${detail.item_key}`, detail);
  await publishStatus('Server unavailable', 'unavailable');
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  clients.push(client);
  client.setQueryData(['review-detail', detail.item_key], detail);
  onlineManager.setOnline(false);
  vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false);
  render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/library?item_key=' + detail.item_key]}><AnnotationVerdict /></MemoryRouter></QueryClientProvider>);
  const panel = within(screen.getAllByRole('heading', { name: 'Your verdict', exact: true })[0].parentElement);
  if (operation === 'set') {
    fireEvent.click(panel.getByRole('button', { name: 'Should read', exact: true }));
    fireEvent.click(panel.getByRole('button', { name: 'Save verdict', exact: true }));
  } else {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    fireEvent.click(panel.getByRole('button', { name: 'Delete', exact: true }));
  }
  await waitFor(async () => expect((await pendingMutations()).some(row => row.item_key === detail.item_key && row.operation === operation)).toBe(true));
});
