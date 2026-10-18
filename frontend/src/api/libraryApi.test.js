import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  askPaper,
  buildPaperRender,
  fetchPaperRender,
  fetchReviewQuestions,
  saveReviewQuestion,
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

  it('sends the persisted question identity and origin to Ask Paper', async () => {
    mockFetch({ answer: '12 pages', question_saved: true });
    await askPaper('K1', 'How many pages?', {
      submissionId: '00000000-0000-4000-8000-000000000048',
      origin: 'preset', presetId: 'page_count', mode: 'retrieval', history: [{ question: 'Earlier?' }],
    });
    const [url, opts] = global.fetch.mock.calls[0];
    expect(url).toBe('/api/library/ask');
    expect(JSON.parse(opts.body)).toEqual({
      item_key: 'K1', question: 'How many pages?',
      submission_id: '00000000-0000-4000-8000-000000000048',
      origin: 'preset', preset_id: 'page_count', mode: 'retrieval',
      history: [{ question: 'Earlier?' }],
    });
  });

  it('gets and comment-saves questions without invoking Ask Paper', async () => {
    const submission = {
      submission_id: '00000000-0000-4000-8000-000000000048', item_key: 'K1',
      question: 'How many pages?', origin: 'typed', preset_id: null,
    };
    mockFetch({ questions: [submission] });
    await expect(fetchReviewQuestions('K 1')).resolves.toEqual({ questions: [submission] });
    expect(global.fetch.mock.calls[0][0]).toBe('/api/library/questions?item_key=K+1');

    mockFetch({ question_saved: true, question_submission: submission });
    await saveReviewQuestion(submission);
    const [url, opts] = global.fetch.mock.calls[0];
    expect(url).toBe('/api/library/questions');
    expect(opts.method).toBe('POST');
    expect(JSON.parse(opts.body)).toEqual(submission);
  });

  it('builds presentation URL', () => {
    expect(paperPresentationUrl('K 1')).toBe('/api/library/render/K%201/presentation');
    expect(paperRenderPdfUrl('K 1', 'v1')).toBe('/api/library/render/K%201/pdf?v=v1');
  });
});
