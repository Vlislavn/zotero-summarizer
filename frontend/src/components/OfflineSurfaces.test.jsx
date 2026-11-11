// @vitest-environment jsdom
import 'fake-indexeddb/auto';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { publishStatus } from '../offlineStore.js';
import LinksRow from './paper/LinksRow.jsx';
import CollectionEditor from './paper/CollectionEditor.jsx';
import TagOfInterestEditor from './paper/TagOfInterestEditor.jsx';
import PaperReaderPane from './library/PaperReaderPane.jsx';

vi.mock('../api/libraryApi.js', () => ({
  itemPdfUrl: () => '/api/library/pdf/P1', fetchTags: vi.fn(async () => ({ items: [] })),
  updateItemTags: vi.fn(), updateItemCollections: vi.fn(), fetchPaperRender: vi.fn(async () => ({ status: 'missing' })),
  buildPaperRender: vi.fn(), paperPresentationUrl: vi.fn(), paperRenderPdfUrl: vi.fn(), paperFigureUrl: vi.fn(),
}));
let client;
afterEach(async () => {
  cleanup();
  client?.clear();
  vi.restoreAllMocks();
  await publishStatus('', 'connected');
});

function show() {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>
    <LinksRow itemKey="P1" detail={{ has_pdf: true, offline_snapshot: true }} />
    <CollectionEditor itemKey="P1" current={[{ key: 'C1', name: 'Saved collection' }]} />
    <TagOfInterestEditor itemKey="P1" tags={['Saved tag']} />
    <PaperReaderPane itemKey="P1" open hasPdf />
  </QueryClientProvider>);
}

it('keeps compact context visible while server-only paper controls are unavailable offline', () => {
  vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false);
  show();
  expect(screen.getByText(/Compact synced context/)).toBeTruthy();
  expect(screen.getByText(/PDF unavailable/)).toBeTruthy();
  expect(screen.getByText(/Collection changes need the Mac.*Saved collection/)).toBeTruthy();
  expect(screen.getByText('Saved tag')).toBeTruthy();
  expect(screen.getByText(/Tag changes need the Mac/)).toBeTruthy();
  expect(screen.getByText(/Figures & full brief unavailable/)).toBeTruthy();
  expect(screen.queryAllByRole('button')).toEqual([]);
  expect(screen.queryAllByRole('link')).toEqual([]);
});

it('remembers an unreachable Mac across navigation even when browser connectivity stays online', async () => {
  vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(true);
  await publishStatus('Server unavailable', 'unavailable');
  show();
  expect(screen.queryByRole('link', { name: 'PDF ↗' })).toBeNull();
  await act(async () => { await publishStatus('', 'syncing'); });
  expect(screen.queryByRole('link', { name: 'PDF ↗' })).toBeNull();
  await act(async () => { await publishStatus('', 'connected'); });
  expect(screen.getByRole('link', { name: 'PDF ↗' }).getAttribute('href')).toBe('/api/library/pdf/P1');
});
