import { afterEach, describe, expect, it, vi } from 'vitest';
import { request } from './client';

function mockFetch(status, body, { json = true } = {}) {
  const text = json ? JSON.stringify(body) : body;
  global.fetch = vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    statusText: 'STATUS',
    text: () => Promise.resolve(text),
  });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe('request', () => {
  it('parses a JSON body on success', async () => {
    mockFetch(200, { hello: 'world' });
    await expect(request('/api/x')).resolves.toEqual({ hello: 'world' });
  });

  it('sends Content-Type: application/json by default', async () => {
    mockFetch(200, {});
    await request('/api/x');
    const [, opts] = global.fetch.mock.calls[0];
    expect(opts.headers['Content-Type']).toBe('application/json');
  });

  it('throws an ApiError carrying status + parsed body on non-2xx', async () => {
    mockFetch(404, { message: 'nope' });
    await expect(request('/api/x')).rejects.toMatchObject({
      message: 'nope',
      status: 404,
      body: { message: 'nope' },
    });
  });

  it('falls back to an HTTP status message when no body message is present', async () => {
    mockFetch(500, {});
    await expect(request('/api/x')).rejects.toMatchObject({
      message: 'HTTP 500 STATUS',
      status: 500,
    });
  });

  it('wraps a non-JSON body as { _raw }', async () => {
    mockFetch(200, 'plain text', { json: false });
    await expect(request('/api/x')).resolves.toEqual({ _raw: 'plain text' });
  });
});
