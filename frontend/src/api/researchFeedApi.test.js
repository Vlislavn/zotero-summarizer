import { afterEach, expect, it, vi } from 'vitest';
import { researchFeedApi } from './researchFeedApi.js';
afterEach(() => vi.unstubAllGlobals());

it('uses the shared error boundary and transmits only explicit cached-run inputs', async () => {
  const fetch = vi.fn().mockResolvedValue({ ok: true, text: async () => '{"run_id":"weekly-one"}' });
  vi.stubGlobal('fetch', fetch);
  const body = { start: '2026-08-20', end: '2026-08-29', card_budget: 5 };
  expect(await researchFeedApi.run(body)).toEqual({ run_id: 'weekly-one' });
  expect(fetch).toHaveBeenCalledWith('/api/research-feed/run', expect.objectContaining({ method: 'POST', body: JSON.stringify(body) }));
  await researchFeedApi.get('weekly one');
  expect(fetch.mock.calls.at(-1)[0]).toBe('/api/research-feed/runs/weekly%20one');
  expect(researchFeedApi.download('weekly one', 'md')).toBe('/api/research-feed/runs/weekly%20one/download?format=md');
});
