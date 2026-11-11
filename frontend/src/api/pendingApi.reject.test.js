import { afterEach, expect, it, vi } from 'vitest';
import { rejectPending } from './pendingApi.js';

afterEach(() => vi.unstubAllGlobals());

it.each([{}, null, { updated: 0 }, { updated: 1 }, { updated: 3 }, { updated: '2' }, { updated: true }])(
  'refuses a missing/partial/contradictory rejection receipt %j', async (receipt) => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(receipt))));
    await expect(rejectPending([1, 2])).rejects.toThrow();
    expect(fetch).toHaveBeenCalledTimes(1);
    const [url, options] = fetch.mock.calls[0];
    expect(url).toBe('/api/pending/reject');
    expect(JSON.parse(options.body)).toEqual({ change_ids: [1, 2] });
  },
);

it('accepts only the actual complete count for captured distinct IDs', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ updated: 2 }))));
  await expect(rejectPending([1, 2])).resolves.toEqual({ updated: 2 });
});

it('matches the API normalization of duplicate explicit IDs', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ updated: 1 }))));
  await expect(rejectPending([1, 1])).resolves.toEqual({ updated: 1 });
});
