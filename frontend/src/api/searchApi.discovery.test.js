import { afterEach, expect, it, vi } from 'vitest';
import { getSession } from './searchApi.js';

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it.each([false, true])('keeps the 15 second timeout with an optional owner: %s', async withOwner => {
  const timeout = new AbortController();
  const owner = new AbortController();
  const timeoutSpy = vi.spyOn(AbortSignal, 'timeout').mockReturnValue(timeout.signal);
  const fetch = vi.fn(async () => new Response(JSON.stringify({ id: 'SESSION 1' })));
  vi.stubGlobal('fetch', fetch);

  await getSession('SESSION 1', withOwner ? { signal: owner.signal } : undefined);

  expect(timeoutSpy).toHaveBeenCalledWith(15_000);
  expect(fetch.mock.calls[0][0]).toBe('/api/search/SESSION%201');
  const signal = fetch.mock.calls[0][1].signal;
  expect(signal.aborted).toBe(false);
  if (withOwner) owner.abort();
  else timeout.abort();
  expect(signal.aborted).toBe(true);
});

it('the timeout still aborts when a caller supplies a live owner', async () => {
  const timeout = new AbortController();
  vi.spyOn(AbortSignal, 'timeout').mockReturnValue(timeout.signal);
  const fetch = vi.fn(async () => new Response('{}'));
  vi.stubGlobal('fetch', fetch);

  await getSession('s', { signal: new AbortController().signal });
  timeout.abort();

  expect(fetch.mock.calls[0][1].signal.aborted).toBe(true);
});
