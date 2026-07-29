import { describe, it, expect } from 'vitest';
import {
  isCoolUndecided, coolUndecidedKeys, scoreToBand,
  sortQueue, serializeSort, hydrateSort, DEFAULT_SORT,
} from './relevanceBands.js';

describe('isCoolUndecided — the auto-review work-list predicate', () => {
  it('counts a must/should-read pick with no proposal and no label', () => {
    expect(isCoolUndecided({ relevance_score: 4.6 })).toBe(true);   // must_read
    expect(isCoolUndecided({ relevance_score: 3.6 })).toBe(true);   // should_read
  });

  it('excludes lower bands', () => {
    expect(isCoolUndecided({ relevance_score: 3.0 })).toBe(false);  // could_read
    expect(isCoolUndecided({ relevance_score: 1.0 })).toBe(false);  // dont_read
    expect(isCoolUndecided({ relevance_score: null })).toBe(false); // unscored
    expect(isCoolUndecided({})).toBe(false);
  });

  it('excludes already-decided picks (so the loop terminates and never re-chews)', () => {
    expect(isCoolUndecided({ relevance_score: 4.6, proposed_verdict: { proposed: 'must_read' } })).toBe(false);
    expect(isCoolUndecided({ relevance_score: 4.6, user_priority: 'should_read' })).toBe(false);
  });

  it('agrees with the band thresholds it filters on', () => {
    expect(scoreToBand(4.6)).toBe('must_read');
    expect(scoreToBand(3.6)).toBe('should_read');
    expect(scoreToBand(3.0)).toBe('could_read');
  });
});

describe('coolUndecidedKeys — the pinned-keys work-list the loop hands the fleet', () => {
  const rows = [
    { item_key: 'A', relevance_score: 4.6 },                               // cool
    { item_key: 'B', relevance_score: 2.9 },                               // could (excluded)
    { item_key: 'C', relevance_score: 3.6, proposed_verdict: { proposed: 'should_read' } }, // decided (excluded)
    { item_key: 'D', relevance_score: 3.7 },                               // cool, deep in queue
  ];

  it('returns only cool-undecided keys, in queue order', () => {
    expect(coolUndecidedKeys(rows)).toEqual(['A', 'D']);
  });

  it('is empty for no items / all-decided, so the loop terminates', () => {
    expect(coolUndecidedKeys([])).toEqual([]);
    expect(coolUndecidedKeys(undefined)).toEqual([]);
    expect(coolUndecidedKeys([{ item_key: 'X', relevance_score: 2.0 }])).toEqual([]);
  });

  it('supports the attempted-ledger dedup the loop relies on', () => {
    const attempted = new Set(['A']);
    const next = coolUndecidedKeys(rows).filter((k) => !attempted.has(k));
    expect(next).toEqual(['D']);  // A already dispatched this session → not re-chewed
  });
});

describe('sortQueue — explicit Zotero-column sort', () => {
  const rows = [
    { item_key: 'A', title: 'beta', authors: 'Zed', venue: 'Nature', publication_date: 'May 2024', date_added: '2026-01-02 10:00:00' },
    { item_key: 'B', title: 'Alpha', authors: 'Ada', venue: '', publication_date: '2022-03-01', date_added: '2026-01-03 10:00:00' },
    { item_key: 'C', title: 'gamma', authors: '', venue: 'Cell', publication_date: '', date_added: '' },
  ];
  const keys = (items) => items.map((i) => i.item_key);

  it("'best' is the identity — the blended server order is untouched", () => {
    expect(sortQueue(rows, { field: 'best', dir: 'desc' })).toBe(rows);
    expect(sortQueue(rows, null)).toBe(rows);
  });

  it('sorts title case-insensitively, both directions, without mutating input', () => {
    const before = keys(rows);
    expect(keys(sortQueue(rows, { field: 'title', dir: 'asc' }))).toEqual(['B', 'A', 'C']);
    expect(keys(sortQueue(rows, { field: 'title', dir: 'desc' }))).toEqual(['C', 'A', 'B']);
    expect(keys(rows)).toEqual(before);
  });

  it('missing values sink to the end in BOTH directions (empty cell never leads)', () => {
    expect(keys(sortQueue(rows, { field: 'creator', dir: 'asc' }))).toEqual(['B', 'A', 'C']);
    expect(keys(sortQueue(rows, { field: 'creator', dir: 'desc' }))).toEqual(['A', 'B', 'C']);
    expect(keys(sortQueue(rows, { field: 'publication', dir: 'asc' }))).toEqual(['C', 'A', 'B']);
  });

  it('year parses the first 4-digit run out of the free-form Zotero date', () => {
    expect(keys(sortQueue(rows, { field: 'year', dir: 'desc' }))).toEqual(['A', 'B', 'C']);
    expect(keys(sortQueue(rows, { field: 'year', dir: 'asc' }))).toEqual(['B', 'A', 'C']);
  });

  it('dateAdded orders lexicographically (Zotero timestamps are sortable strings)', () => {
    expect(keys(sortQueue(rows, { field: 'dateAdded', dir: 'desc' }))).toEqual(['B', 'A', 'C']);
  });
});

describe('sort URL (de)serialize — compact keys, defaults omitted', () => {
  it('default sort serializes to nothing; hydrate of nothing is the default', () => {
    expect(serializeSort(DEFAULT_SORT)).toEqual({});
    expect(hydrateSort(new URLSearchParams(''))).toEqual(DEFAULT_SORT);
  });

  it('field-default direction is omitted; explicit override round-trips', () => {
    expect(serializeSort({ field: 'title', dir: 'asc' })).toEqual({ s: 'title' });
    expect(serializeSort({ field: 'title', dir: 'desc' })).toEqual({ s: 'title', sd: 'desc' });
    expect(hydrateSort(new URLSearchParams('s=title'))).toEqual({ field: 'title', dir: 'asc' });
    expect(hydrateSort(new URLSearchParams('s=year&sd=asc'))).toEqual({ field: 'year', dir: 'asc' });
  });

  it('an unknown field in the URL falls back to the default sort', () => {
    expect(hydrateSort(new URLSearchParams('s=nope'))).toEqual(DEFAULT_SORT);
  });
});
