import { afterEach, expect, it, vi } from 'vitest';
import { screen } from './searchApi.js';

afterEach(() => vi.unstubAllGlobals());

it('keeps the default request unchanged and forwards only an explicit evidence policy', async () => {
  const fetch = vi.fn(async () => new Response('{}'));
  vi.stubGlobal('fetch', fetch);
  await screen({ query: 'topic', questions: ['question'] });
  await screen({ query: 'topic', questions: ['question'], evidence_policy: 'question_evidence' });
  expect(fetch.mock.calls.map(([, options]) => JSON.parse(options.body))).toEqual([
    { query: 'topic', questions: ['question'] },
    { query: 'topic', questions: ['question'], evidence_policy: 'question_evidence' },
  ]);
});
