import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  askPaper,
  buildPaperRender,
  fetchPaperRender,
  fetchRelatedPapers,
  paperPresentationUrl,
  paperRenderPdfUrl,
  runReviewFleet,
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

it('carries the related-query owner cancellation signal through the GET wrapper', async () => {
  mockFetch({ status: 'ready', items: [] });
  const owner = new AbortController();
  await fetchRelatedPapers('SEED 1', { signal: owner.signal });
  const [url, options] = global.fetch.mock.calls[0];
  expect(url).toBe('/api/library/related/SEED%201');
  expect(options.signal).toBe(owner.signal);
  expect(options.method).toBeUndefined();
  owner.abort();
  expect(options.signal.aborted).toBe(true);
});

describe('explicit fleet selection', () => {
  it.each([
    [{}, { top_k: 5 }],
    [{ topK: 2, itemKeys: null }, { top_k: 2 }],
    [{ topK: 2, itemKeys: [] }, { item_keys: [] }],
    [{ itemKeys: ['B', 'A', 'B'] }, { item_keys: ['B', 'A', 'B'] }],
  ])('preserves selection in %j', async (options, expected) => {
    mockFetch({ accepted: false });
    await runReviewFleet(options);
    const [url, init] = global.fetch.mock.calls[0];
    expect(url).toBe('/api/library/review-fleet/run');
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body)).toEqual(expected);
  });
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
