import { describe, it, expect } from 'vitest';
import { isCoolUndecided, coolUndecidedKeys, scoreToBand } from './relevanceBands.js';

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
