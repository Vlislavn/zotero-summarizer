// @vitest-environment jsdom
import 'fake-indexeddb/auto';
import { IDBFactory } from 'fake-indexeddb';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

beforeEach(() => {
  vi.resetModules();
  vi.stubGlobal('indexedDB', new IDBFactory());
  Object.defineProperty(navigator, 'onLine', { configurable: true, value: true });
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it.each([403, 200])('does not grant outage authority after received HTTP %s headers when its body fails', async (status) => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: status === 200, status, statusText: 'Owned response',
    text: async () => { throw new TypeError('Truncated received body'); },
    json: async () => { throw new TypeError('Truncated received body'); } }));
  const offline = await import('./offlineStore.js');
  let result;
  const listener = (event) => { result = event.detail; };
  window.addEventListener(offline.syncStatusEvent, listener);
  try {
    await (await import('./syncClient.js')).syncNow();
    expect(result.connection).toBe('blocked');
    expect(offline.transportUnavailable()).toBe(false);
    expect(await offline.pendingMutations()).toEqual([]);
  } finally { window.removeEventListener(offline.syncStatusEvent, listener); }
});

it('still distinguishes a genuine fetch rejection before any response', async () => {
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Owned connection refused')));
  const offline = await import('./offlineStore.js');
  await (await import('./syncClient.js')).syncNow();
  expect(offline.transportUnavailable()).toBe(true);
});
