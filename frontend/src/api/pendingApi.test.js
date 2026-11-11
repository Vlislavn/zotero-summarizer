import { afterEach, expect, it, vi } from 'vitest';
import { applyPending, fetchPending, rejectPending } from './pendingApi.js';

afterEach(() => vi.unstubAllGlobals());

it.each([
  ['ordinary', [7], { 7: 'a'.repeat(64) }, {}],
  ['generated common envelope', [151], { 151: 'd'.repeat(64) }, {}],
  ['mixed', [7, 151], { 7: 'a'.repeat(64), 151: 'd'.repeat(64) }, {}],
  ['explicit retry', [7], { 7: 'b'.repeat(64) }, { retry: true }],
  ['force', [7, 151], { 7: 'a'.repeat(64), 151: 'd'.repeat(64) }, { force: true }],
  ['forced retry', [151], { 151: 'd'.repeat(64) }, { force: true, retry: true }],
])('posts the exact caller-presented %s map without fetching or minting authority', async (_label, ids, previews, flags) => {
  const before = JSON.stringify(previews);
  const fetch = vi.fn(async () => new Response(JSON.stringify({ applied: ids.length, failed: 0 })));
  vi.stubGlobal('fetch', fetch);

  await applyPending(ids, { previews, ...flags });

  expect(fetch).toHaveBeenCalledTimes(1);
  expect(fetch).toHaveBeenCalledWith('/api/pending/apply', {
    headers: { 'Content-Type': 'application/json' }, method: 'POST',
    body: JSON.stringify({ change_ids: ids, previews, force: false, retry: false, ...flags }),
  });
  expect(JSON.stringify(previews)).toBe(before);
});

it.each([undefined, {}, { previews: undefined }, { previews: null }, { previews: {} }, { previews: [] }])(
  'refuses missing approval authority before HTTP: %j', async (options) => {
    const fetch = vi.fn();
    vi.stubGlobal('fetch', fetch);
    await expect(applyPending([7], options)).rejects.toBeInstanceOf(TypeError);
    expect(fetch).not.toHaveBeenCalled();
  },
);

it('keeps Reject ID-only and separate from Apply authority', async () => {
  const fetch = vi.fn(async () => new Response(JSON.stringify({ updated: 1 })));
  vi.stubGlobal('fetch', fetch);
  await rejectPending([7]);
  expect(fetch).toHaveBeenCalledWith('/api/pending/reject', {
    headers: { 'Content-Type': 'application/json' }, method: 'POST', body: JSON.stringify({ change_ids: [7] }),
  });
});

it('does not retry a request rejected by the transport', async () => {
  const fetch = vi.fn().mockRejectedValue(new Error('Network disconnected'));
  vi.stubGlobal('fetch', fetch);

  await expect(fetchPending()).rejects.toThrow('Network disconnected');

  expect(fetch).toHaveBeenCalledTimes(1);
});

it.each([
  ['empty 204', 204, ''],
  ['malformed successful body', 200, '{not json'],
])('preserves %s decoding with one request', async (_label, status, text) => {
  const fetch = vi.fn(async () => ({
    ok: status >= 200 && status < 300,
    status,
    statusText: 'STATUS',
    text: async () => text,
  }));
  vi.stubGlobal('fetch', fetch);

  await expect(fetchPending()).resolves.toEqual(status === 204 ? null : { _raw: text });

  expect(fetch).toHaveBeenCalledTimes(1);
});

it('preserves an ordinary decoded HTTP error with one request', async () => {
  const fetch = vi.fn(async () => ({
    ok: false,
    status: 503,
    statusText: 'Unavailable',
    text: async () => JSON.stringify({ detail: 'Temporarily unavailable' }),
  }));
  vi.stubGlobal('fetch', fetch);

  await expect(fetchPending()).rejects.toMatchObject({
    message: 'Temporarily unavailable',
    status: 503,
    body: { detail: 'Temporarily unavailable' },
  });

  expect(fetch).toHaveBeenCalledTimes(1);
});

it('retains a fully read empty HTTP error body as null with one request', async () => {
  const fetch = vi.fn(async () => ({
    ok: false,
    status: 409,
    statusText: 'Conflict',
    text: async () => '',
  }));
  vi.stubGlobal('fetch', fetch);

  const error = await applyPending([7], { previews: { 7: 'a'.repeat(64) } }).catch((failure) => failure);

  expect(error).toMatchObject({ status: 409, body: null });
  expect(Object.hasOwn(error, 'body')).toBe(true);
  expect(fetch).toHaveBeenCalledTimes(1);
});
