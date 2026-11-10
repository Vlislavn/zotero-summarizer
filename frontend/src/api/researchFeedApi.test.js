import { afterEach, expect, it, vi } from 'vitest';
import { researchFeedApi, telegramFeedApi } from './researchFeedApi.js';

afterEach(() => vi.unstubAllGlobals());

it('reads saved reports with encoded identities and no generation command', async () => {
  const fetch = vi.fn().mockResolvedValue({ ok: true, text: async () => '{"items":[]}' });
  vi.stubGlobal('fetch', fetch);
  await researchFeedApi.list();
  await researchFeedApi.get('weekly-a/b?');
  expect(fetch.mock.calls.map(([path]) => path)).toEqual([
    '/api/research-feed/runs?limit=100', '/api/research-feed/runs/weekly-a%2Fb%3F',
  ]);
  expect(fetch.mock.calls.every(([, options]) => !options.method || options.method === 'GET')).toBe(true);
  expect(researchFeedApi.download('weekly-a/b?', 'md')).toBe('/api/research-feed/runs/weekly-a%2Fb%3F/download?format=md');
});

it('keeps source saves, status reads and explicit consented runs separate', async () => {
  const fetch = vi.fn().mockResolvedValue({ ok: true, text: async () => '{}' });
  vi.stubGlobal('fetch', fetch);
  await telegramFeedApi.sources(); await telegramFeedApi.status();
  await telegramFeedApi.save({ sources: [] }); await telegramFeedApi.collectConsent(true); await telegramFeedApi.run();
  expect(fetch.mock.calls.map(([path, options]) => [path, options.method || 'GET'])).toEqual([
    ['/api/research-feed/telegram/sources', 'GET'], ['/api/research-feed/telegram/status', 'GET'],
    ['/api/research-feed/telegram/sources', 'PUT'], ['/api/research-feed/telegram/collect-consent', 'POST'],
    ['/api/research-feed/telegram/runs', 'POST'],
  ]);
  expect(JSON.parse(fetch.mock.calls[3][1].body)).toEqual({ allow: true });
});

it('keeps report failures as errors instead of an empty report', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 422,
    text: async () => '{"error":"report_unavailable","message":"Saved weekly digest is unreadable or unsupported"}' }));
  await expect(researchFeedApi.get('weekly-bad')).rejects.toMatchObject({ status: 422, body: { error: 'report_unavailable' } });
});
