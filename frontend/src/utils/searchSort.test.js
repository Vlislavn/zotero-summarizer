import { expect, it } from 'vitest';
import { sortSearchCandidates } from './searchSort.js';

it('blends known citation prestige, keeps missing scores last, and never mutates the session', () => {
  const rows = [
    { title: 'relevant', query_score: 0.9, cited_by_count: 0 },
    { title: 'established', query_score: 0.85, cited_by_count: 100 },
    { title: 'unknown', query_score: 0.85, cited_by_count: null },
    { title: 'off-topic', query_score: 0.1, cited_by_count: 10000 },
    { title: 'unscored', query_score: null, cited_by_count: 100000 },
    { title: 'retracted', query_score: 1, cited_by_count: 100000, is_retracted: true },
  ];
  expect(sortSearchCandidates(rows, 'recommended')).toBe(rows);
  expect(sortSearchCandidates(rows, 'relevance').map((r) => r.title)).toEqual([
    'relevant', 'established', 'unknown', 'off-topic', 'unscored', 'retracted',
  ]);
  const blended = sortSearchCandidates(rows.slice(0, 4), 'relevance_prestige');
  expect(blended.map((r) => r.title)).toEqual(['established', 'unknown', 'relevant', 'off-topic']);
  expect(rows[0].title).toBe('relevant');
  const noCitations = rows.map((row) => ({ ...row, cited_by_count: null }));
  expect(sortSearchCandidates(noCitations, 'relevance_prestige')).toEqual(sortSearchCandidates(noCitations, 'relevance'));
});
