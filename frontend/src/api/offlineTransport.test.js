// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { offlineRequest, offlineRequestBudget } from './offlineTransport.js';
beforeEach(() => { vi.useFakeTimers(); vi.stubEnv('VITE_SYNC_TIMEOUT_MS', '15000'); });
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); });

it.each(['0', 'NaN', '60001'])('refuses invalid budget %s before any network effect', async (value) => {
  vi.stubEnv('VITE_SYNC_TIMEOUT_MS', value);
  vi.stubGlobal('fetch', vi.fn());
  await expect(offlineRequest('/owned', {}, () => null)).rejects.toThrow('Invalid sync timeout budget');
  expect(fetch).not.toHaveBeenCalled();
});

it('bounds an unreachable response without waiting for the browser TCP deadline', async () => {
  vi.stubGlobal('fetch', vi.fn((_path, { signal }) => new Promise((_resolve, reject) => {
    signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true });
  })));
  const reading = offlineRequest('/owned', {}, () => null);
  const result = expect(reading).rejects.toMatchObject({ transportFailure: true });
  await vi.advanceTimersByTimeAsync(offlineRequestBudget());
  await result;
  expect(vi.getTimerCount()).toBe(0);
});

it('does not turn a malformed received acknowledgement into a transport failure', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true }));
  const failure = new SyntaxError('Invalid received acknowledgement');
  await expect(offlineRequest('/owned', {}, () => { throw failure; })).rejects.toBe(failure);
  expect(failure.transportFailure).toBeUndefined();
  expect(vi.getTimerCount()).toBe(0);
});

it('does not reinterpret caller cancellation as new offline intent', async () => {
  const caller = new AbortController();
  caller.abort();
  vi.stubGlobal('fetch', vi.fn());
  await expect(offlineRequest('/owned', { signal: caller.signal }, () => null))
    .rejects.toMatchObject({ name: 'AbortError' });
  expect(fetch).not.toHaveBeenCalled();
  expect(vi.getTimerCount()).toBe(0);
});
