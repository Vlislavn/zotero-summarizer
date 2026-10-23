// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import RssFeedsSection from './RssFeedsSection.jsx';

const SOURCES = [
  { name: 'HackerNoon AI Agents', url: 'https://hackernoon.com/tagged/ai-agents/feed' },
  { name: 'HackerNoon Agentic AI', url: 'https://hackernoon.com/tagged/agentic-ai/feed' },
];
let feeds;
let refresh;
let client;

function response(data, status = 200) {
  const body = JSON.stringify(data);
  return { ok: status < 400, status, text: async () => body, json: async () => JSON.parse(body) };
}

function mount() {
  client = new QueryClient({ defaultOptions: {
    queries: { retry: false, refetchOnWindowFocus: false }, mutations: { retry: false },
  } });
  return render(<QueryClientProvider client={client}><RssFeedsSection /></QueryClientProvider>);
}

beforeEach(() => {
  feeds = [];
  refresh = () => response({ feeds: 2, inserted: 0, updated: 0, errors: [] });
  vi.stubGlobal('fetch', vi.fn(async (path, options = {}) => {
    if (path === '/api/rss/feeds' && !options.method) return response({ feeds: [...feeds] });
    if (path === '/api/rss/feeds' && options.method === 'POST') {
      const saved = { id: feeds.length + 1, source: 'app', ...JSON.parse(options.body) };
      feeds.push(saved);
      return response(saved);
    }
    if (path === '/api/rss/refresh') return refresh();
    throw new Error(`Unexpected request: ${options.method} ${path}`);
  }));
});

afterEach(() => {
  cleanup();
  client?.clear();
  vi.unstubAllGlobals();
});

it('explicitly adds both exact sources via the common RSS API, preserving a custom draft', async () => {
  mount();
  await screen.findByText('No RSS feeds saved.');
  expect(fetch.mock.calls.every(([, options]) => !options.method)).toBe(true);
  fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'My custom source' } });
  fireEvent.change(screen.getByLabelText('URL'), { target: { value: 'https://example.org/feed' } });

  for (const source of SOURCES) {
    fireEvent.click(screen.getByRole('button', { name: `Add ${source.name}` }));
    await waitFor(() => expect(screen.getByRole('button', { name: `${source.name} saved` }).disabled).toBe(true));
  }

  const posts = fetch.mock.calls.filter(([path, options]) => path === '/api/rss/feeds' && options.method === 'POST');
  expect(posts.map(([, options]) => JSON.parse(options.body))).toEqual(SOURCES.map((source) => ({ ...source, enabled: true })));
  expect(screen.getAllByLabelText('Name')[0].value).toBe('My custom source');
  expect(screen.getAllByLabelText('URL')[0].value).toBe('https://example.org/feed');
  expect(screen.getAllByText('Not refreshed yet')).toHaveLength(2);
  expect(screen.getByRole('link', { name: 'Today → Triage backlog' }).getAttribute('href')).toBe('/today');
  expect(fetch.mock.calls.some(([path]) => path === '/api/rss/refresh')).toBe(false);
});

it('does not duplicate or re-enable an existing disabled source', async () => {
  feeds = [{ id: 1, ...SOURCES[0], enabled: false }];
  mount();

  const saved = await screen.findByRole('button', { name: 'HackerNoon AI Agents saved (disabled)' });
  expect(saved.disabled).toBe(true);
  fireEvent.click(saved);
  expect(fetch.mock.calls.some(([, options]) => options.method)).toBe(false);
  expect(screen.getByRole('button', { name: 'Add HackerNoon Agentic AI' }).disabled).toBe(false);
});

it('blocks additional subscriptions while an add is pending', async () => {
  let finish;
  const original = fetch.getMockImplementation();
  fetch.mockImplementation((path, options = {}) => {
    if (options.method === 'POST') return new Promise((resolve) => { finish = () => resolve(response({ ...SOURCES[0], id: 1 })); });
    return original(path, options);
  });
  mount();
  await screen.findByText('No RSS feeds saved.');
  fireEvent.click(screen.getByRole('button', { name: 'Add HackerNoon AI Agents' }));

  await waitFor(() => expect(screen.getByRole('button', { name: 'Add HackerNoon Agentic AI' }).disabled).toBe(true));
  expect(screen.getByRole('button', { name: 'Add feed' }).disabled).toBe(true);
  expect(screen.getByRole('button', { name: 'Adding HackerNoon AI Agents…' }).disabled).toBe(true);
  finish();
  await screen.findByText(/Feed saved/);
});

it('distinguishes unavailable subscriptions from an empty list and offers a read retry', async () => {
  fetch.mockResolvedValueOnce(response({ message: 'Synthetic source-list unavailable' }, 503));
  mount();

  await screen.findByText(/Synthetic source-list unavailable/);
  expect(screen.queryByText('No RSS feeds saved.')).toBeNull();
  expect(screen.getByRole('button', { name: 'Add HackerNoon AI Agents' }).disabled).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: 'Retry loading feeds' }));
  await screen.findByText('No RSS feeds saved.');
  expect(screen.getByRole('button', { name: 'Add HackerNoon AI Agents' }).disabled).toBe(false);
});

it('reports zero returned items without claiming a triage or a successful story', async () => {
  mount();
  await screen.findByText('No RSS feeds saved.');
  fireEvent.click(screen.getByRole('button', { name: 'Refresh RSS now' }));

  await screen.findByText('Checked 2 feeds: 0 new, 0 updated. No items stored in this pass.');
  const call = fetch.mock.calls.find(([path]) => path === '/api/rss/refresh');
  expect(JSON.parse(call[1].body)).toEqual({ max_feeds: 10, max_new_items_per_feed: 25 });
  expect(fetch.mock.calls.some(([path]) => path.includes('triage-backlog'))).toBe(false);
});

it('reports offline refresh as skipped rather than a successful empty fetch', async () => {
  refresh = () => response({ feeds: 0, inserted: 0, updated: 0, errors: [], offline: true });
  mount();
  fireEvent.click(await screen.findByRole('button', { name: 'Refresh RSS now' }));

  await screen.findByText('RSS refresh skipped: the app is offline. Sources were not checked.');
  expect(screen.queryByText(/Checked 0 feeds/)).toBeNull();
});

it('reloads persisted feed errors even when every refresh fails, and clears the error on retry', async () => {
  feeds = [{ id: 1, ...SOURCES[0], enabled: true }];
  refresh = () => {
    feeds = [{ ...feeds[0], last_fetched_at: '2026-10-08T12:00:00Z', last_error: 'HTTPStatusError: 403 Forbidden' }];
    return response({ message: 'RSS refresh failed; check the feed error' }, 502);
  };
  mount();
  await screen.findByText('Not refreshed yet');
  fireEvent.click(screen.getByRole('button', { name: 'Refresh RSS now' }));

  await screen.findByText('Last error: HTTPStatusError: 403 Forbidden');
  expect(screen.getByText('Last checked: 2026-10-08T12:00:00Z')).toBeTruthy();
  refresh = () => {
    feeds = [{ ...feeds[0], last_error: null }];
    return response({ feeds: 1, inserted: 0, updated: 0, errors: [] });
  };
  fireEvent.click(screen.getByRole('button', { name: 'Refresh RSS now' }));
  await screen.findByText(/Checked 1 feed: 0 new, 0 updated/);
  await waitFor(() => expect(screen.queryByText(/Last error:/)).toBeNull());
  expect(screen.queryByText(/RSS refresh failed/)).toBeNull();
});

it('retains the ordinary custom-feed save flow', async () => {
  mount();
  await screen.findByText('No RSS feeds saved.');
  fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Custom source' } });
  fireEvent.change(screen.getByLabelText('URL'), { target: { value: 'https://example.org/feed' } });
  fireEvent.click(screen.getByRole('button', { name: 'Add feed' }));

  await waitFor(() => expect(screen.getAllByLabelText('Name')[0].value).toBe(''));
  expect(feeds).toEqual([{ id: 1, source: 'app', name: 'Custom source', url: 'https://example.org/feed', enabled: true }]);
});

it('allows retrying a failed preset without a stale error after success', async () => {
  mount();
  await screen.findByText('No RSS feeds saved.');
  fetch.mockResolvedValueOnce(response({ message: 'Synthetic save failed' }, 422));
  fireEvent.click(screen.getByRole('button', { name: 'Add HackerNoon AI Agents' }));
  await screen.findByText('Synthetic save failed');
  expect(feeds).toEqual([]);

  fireEvent.click(screen.getByRole('button', { name: 'Add HackerNoon AI Agents' }));
  await screen.findByRole('button', { name: 'HackerNoon AI Agents saved' });
  expect(screen.queryByText('Synthetic save failed')).toBeNull();
});

it('keeps partial failure distinct from a clean refresh', async () => {
  refresh = () => response({ feeds: 2, inserted: 1, updated: 1, errors: [{ feed_id: 2, error: '403' }] });
  mount();
  fireEvent.click(await screen.findByRole('button', { name: 'Refresh RSS now' }));

  await screen.findByText('Checked 2 feeds: 1 new, 1 updated. 1 feed(s) failed — see their errors below.');
});
