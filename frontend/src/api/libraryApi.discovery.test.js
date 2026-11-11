import { afterEach, expect, it, vi } from 'vitest';
import { fetchDiscovery, findNewRelevant } from './libraryApi.js';

afterEach(() => vi.unstubAllGlobals());

it('reads independent metadata without POST and forwards owner cancellation', async () => {
  const dto = { seed_key: 'SEED 1', seed_title: 'Seed', admission: { state: 'saved', basis: 'native' },
    context_fingerprint: 'a'.repeat(64), boundary: { enabled: true, provider: 'fixture-provider', model: 'fixture-model', token: 'b'.repeat(64) },
    private_context: { status: 'observed', record_count: 1 } };
  const fetch = vi.fn(async () => new Response(JSON.stringify(dto)));
  vi.stubGlobal('fetch', fetch);
  const owner = new AbortController();

  await expect(fetchDiscovery('SEED 1', { signal: owner.signal })).resolves.toEqual(dto);

  expect(fetch).toHaveBeenCalledTimes(1);
  const [path, init] = fetch.mock.calls[0];
  expect(path).toBe('/api/library/related/SEED%201/discover');
  expect(init.method).toBeUndefined();
  expect(init.body).toBeUndefined();
  expect(init.signal).toBe(owner.signal);
});

it.each([{}, { seed_key: 'OTHER', admission: { state: 'saved' }, context_fingerprint: 'a'.repeat(64) },
  { seed_key: 'SEED', admission: { state: 'saved', basis: 'native' }, context_fingerprint: 'a'.repeat(64) },
])('rejects malformed/wrong-seed metadata without a positive result: %j', async dto => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(dto))));

  await expect(fetchDiscovery('SEED')).rejects.toBeInstanceOf(Error);
});

it.each([null, 'b'.repeat(64)])('maps only the frozen POST fields with consent %s', async consentToken => {
  const fetch = vi.fn(async () => new Response(JSON.stringify({ id: 'retained-id' })));
  vi.stubGlobal('fetch', fetch);
  const owner = new AbortController();

  await findNewRelevant('SEED/1', { contextFingerprint: 'a'.repeat(64), consentToken, retry: true, signal: owner.signal });

  const [path, init] = fetch.mock.calls[0];
  expect(path).toBe('/api/library/related/SEED%2F1/discover');
  expect(init.method).toBe('POST');
  expect(init.signal).toBe(owner.signal);
  expect(JSON.parse(init.body)).toEqual({ context_fingerprint: 'a'.repeat(64), consent_token: consentToken, retry: true });
});

it('defaults to public-only/no-retry and preserves typed refusals', async () => {
  const fetch = vi.fn(async () => new Response(JSON.stringify({ error: 'not_saved', message: 'Save first' }), { status: 409 }));
  vi.stubGlobal('fetch', fetch);

  await expect(findNewRelevant('SEED', { contextFingerprint: 'a'.repeat(64) })).rejects.toMatchObject({
    status: 409, body: { error: 'not_saved' }, message: 'Save first',
  });

  expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({ context_fingerprint: 'a'.repeat(64), consent_token: null, retry: false });
  expect(fetch).toHaveBeenCalledTimes(1);
});
