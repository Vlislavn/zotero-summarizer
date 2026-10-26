// @vitest-environment jsdom
import 'fake-indexeddb/auto';
import { IDBFactory } from 'fake-indexeddb';
import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

const transport = vi.hoisted(() => ({ request: vi.fn() }));
vi.mock('./api/client.js', () => ({ request: transport.request }));
beforeEach(() => {
  vi.resetModules();
  transport.request.mockReset();
  vi.stubGlobal('indexedDB', new IDBFactory());
  Object.defineProperty(navigator, 'onLine', { configurable: true, value: true });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it('keeps availability unknown until the actual typed pull and device commit finish', async () => {
  const offline = await import('./offlineStore.js');
  const { default: useServerAvailable } = await import('./hooks/useServerAvailable.js');
  let finish;
  const reply = new Promise((resolve) => { finish = resolve; });
  transport.request.mockReturnValue(reply);
  const hook = renderHook(useServerAvailable);
  expect(hook.result.current).toBe(false);
  const running = (await import('./syncClient.js')).syncNow();
  try {
    await waitFor(() => expect(transport.request).toHaveBeenCalledOnce());
    expect(offline.isServerAvailable()).toBe(false);
    expect(hook.result.current).toBe(false);
  } finally {
    await act(async () => { finish({ protocol: 1, cursor: 1, papers: [] }); await running; });
  }
  await waitFor(() => expect(hook.result.current).toBe(true));
  expect(await offline.getMeta('cursor')).toBe(1);
});

it.each([
  ['transport', Object.assign(new TypeError('Owned connection refused'), { transportFailure: true }), 'unavailable'],
  ['HTTP', Object.assign(new Error('Forbidden'), { status: 403 }), 'blocked'],
])('distinguishes %s failure without enabling server actions', async (_label, error, phase) => {
  const offline = await import('./offlineStore.js');
  transport.request.mockRejectedValue(error);
  let status;
  const update = (event) => { status = event.detail; };
  window.addEventListener(offline.syncStatusEvent, update);
  try {
    await (await import('./syncClient.js')).syncNow();
    expect(status.connection).toBe(phase);
    expect(offline.isServerAvailable()).toBe(false);
    expect(status.sync_error).toBe(phase === 'unavailable' ? 'Server unavailable' : 'Sync request failed (HTTP 403): Forbidden');
  } finally { window.removeEventListener(offline.syncStatusEvent, update); }
});

it('refuses malformed typed pull data instead of turning a reachable response into success or an outage', async () => {
  const offline = await import('./offlineStore.js');
  await offline.applyPull({ cursor: 4, papers: [{ item_key: 'P1', title: 'Keep this' }] });
  transport.request.mockResolvedValue({ protocol: 1, cursor: -1, papers: [] });
  let status;
  const update = (event) => { status = event.detail; };
  window.addEventListener(offline.syncStatusEvent, update);
  try {
    await (await import('./syncClient.js')).syncNow();
    expect(status.connection).toBe('blocked');
    expect(status.sync_error).toBe('Invalid sync snapshot; device state preserved');
    expect(await offline.getMeta('cursor')).toBe(4);
    expect((await offline.allPapers())[0].item_key).toBe('P1');
  } finally { window.removeEventListener(offline.syncStatusEvent, update); }
});
