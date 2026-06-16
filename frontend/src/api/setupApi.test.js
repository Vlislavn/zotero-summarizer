import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  detectZotero,
  fetchSetupStatus,
  updatePaths,
  validateSetup,
} from './setupApi.js';

// settingsApi's request() resolves a success via res.json() (not res.text()),
// so the mock fetch must expose a json() method.
function mockFetch(body = {}, { ok = true, status = 200 } = {}) {
  global.fetch = vi.fn().mockResolvedValue({
    ok,
    status,
    statusText: 'OK',
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe('setup API wrappers', () => {
  it('GETs setup status from /api/setup/status', async () => {
    mockFetch({ ready: false });
    await expect(fetchSetupStatus()).resolves.toEqual({ ready: false });
    expect(global.fetch.mock.calls[0][0]).toBe('/api/setup/status');
    // Default fetch (no options.method) is a GET.
    expect(global.fetch.mock.calls[0][1]?.method).toBeUndefined();
  });

  it('GETs Zotero candidates from /api/setup/detect-zotero', async () => {
    mockFetch({ candidates: [] });
    await expect(detectZotero()).resolves.toEqual({ candidates: [] });
    expect(global.fetch.mock.calls[0][0]).toBe('/api/setup/detect-zotero');
  });

  it('PUTs only the provided path keys to /api/setup/paths', async () => {
    mockFetch({ written: ['zotero_data_dir'], restart_required: true });
    await updatePaths({ zotero_data_dir: '/Users/me/Zotero' });
    const [url, opts] = global.fetch.mock.calls[0];
    expect(url).toBe('/api/setup/paths');
    expect(opts.method).toBe('PUT');
    expect(JSON.parse(opts.body)).toEqual({ zotero_data_dir: '/Users/me/Zotero' });
  });

  it('rejects updatePaths with a non-object body', async () => {
    await expect(updatePaths(null)).rejects.toThrow(/must be an object/);
  });

  it('POSTs config + test_connection flag to /api/setup/validate-config', async () => {
    mockFetch({ valid: true, field_errors: [], connection: null });
    await validateSetup({ config: { research_goals: ['x'] }, test_connection: true });
    const [url, opts] = global.fetch.mock.calls[0];
    expect(url).toBe('/api/setup/validate-config');
    expect(opts.method).toBe('POST');
    expect(JSON.parse(opts.body)).toEqual({
      config: { research_goals: ['x'] },
      test_connection: true,
    });
  });

  it('defaults validateSetup test_connection to false', async () => {
    mockFetch({ valid: true, field_errors: [] });
    await validateSetup({ config: { research_goals: ['x'] } });
    const [, opts] = global.fetch.mock.calls[0];
    expect(JSON.parse(opts.body)).toMatchObject({ test_connection: false });
  });

  it('rejects validateSetup with a non-object config', async () => {
    await expect(validateSetup({ config: undefined })).rejects.toThrow(/must be an object/);
  });
});
