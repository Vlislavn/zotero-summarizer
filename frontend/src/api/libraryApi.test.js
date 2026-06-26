import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  askPaper,
  buildPaperRender,
  fetchPaperRender,
  paperPresentationUrl,
  paperRenderPdfUrl,
} from './libraryApi.js';

function mockFetch(body = {}) {
  global.fetch = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    statusText: 'OK',
    text: () => Promise.resolve(JSON.stringify(body)),
  });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe('paper-render library API wrappers', () => {
  it('fetches paper-render status', async () => {
    mockFetch({ status: 'completed' });
    await expect(fetchPaperRender('K 1')).resolves.toEqual({ status: 'completed' });
    expect(global.fetch.mock.calls[0][0]).toBe('/api/library/render/K%201');
  });

  it('starts a build with explicit acquisition flags', async () => {
    mockFetch({ status: 'running' });
    await buildPaperRender('K1', {
      force: true,
      allowArxivSource: true,
      allowAcquireMissing: true,
    });
    const [, opts] = global.fetch.mock.calls[0];
    expect(opts.method).toBe('POST');
    expect(JSON.parse(opts.body)).toEqual({
      force: true,
      allow_arxiv_source: true,
      allow_acquire_missing: true,
    });
  });

  it('defaults askPaper to comprehensive mode', async () => {
    mockFetch({ answer: '12 pages' });
    await askPaper('K1', 'How many pages?');
    const [, opts] = global.fetch.mock.calls[0];
    expect(JSON.parse(opts.body)).toMatchObject({ mode: 'comprehensive' });
  });

  it('builds presentation URL', () => {
    expect(paperPresentationUrl('K 1')).toBe('/api/library/render/K%201/presentation');
    expect(paperRenderPdfUrl('K 1', 'v1')).toBe('/api/library/render/K%201/pdf?v=v1');
  });
});
