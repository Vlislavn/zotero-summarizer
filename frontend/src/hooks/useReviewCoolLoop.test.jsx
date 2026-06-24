// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';

// Mock the API module the hook drives. The fleet returns are NON-running by
// default so pollFleetUntilDone (which sleeps between polls) is skipped — the
// loop logic (pinning, attempted-ledger, drain, bounds) runs synchronously.
vi.mock('../api/libraryApi.js', () => ({
  runReviewFleet: vi.fn(),
  fetchReviewFleetStatus: vi.fn(),
  fetchReadingQueue: vi.fn(),
}));

import { runReviewFleet, fetchReviewFleetStatus, fetchReadingQueue } from '../api/libraryApi.js';
import { useReviewCoolLoop } from './useReviewCoolLoop.js';

// Two cool (must/should-read) undecided rows + one could_read distractor — the
// exact shape that defeated the old band-agnostic loop (could rows proposed, so
// the loop never terminated). isCoolUndecided/coolUndecidedKeys are the REAL impls.
const COOL = [
  { item_key: 'A', relevance_score: 4.6 },
  { item_key: 'B', relevance_score: 3.7 },
  { item_key: 'C', relevance_score: 2.8 }, // could_read — must never be pinned
];

function setup(overrides = {}) {
  const props = {
    queue: [],
    queueArgs: () => ({ force: false }),
    applyQueueData: vi.fn(),
    loadQueue: vi.fn(),
    zoteroReady: false, // skip the mount-resume poll so the loop tests are isolated
    setMessage: vi.fn(),
    setIsError: vi.fn(),
    ...overrides,
  };
  return { ...renderHook(() => useReviewCoolLoop(props)), props };
}

beforeEach(() => {
  vi.clearAllMocks();
  fetchReadingQueue.mockResolvedValue({ items: COOL });
  fetchReviewFleetStatus.mockResolvedValue({ status: 'ready', completed: 1 });
  runReviewFleet.mockResolvedValue({ status: 'ready', accepted: true, proposed: 1 });
});

describe('useReviewCoolLoop', () => {
  it('pins EXACTLY the cool keys to the fleet (not the band-agnostic could row)', async () => {
    const { result } = setup();
    await act(async () => { await result.current.handleReviewCool(); });
    expect(runReviewFleet).toHaveBeenCalledTimes(1);
    expect(runReviewFleet).toHaveBeenCalledWith({ itemKeys: ['A', 'B'] }); // C (could) excluded
  });

  it('terminates via the attempted-ledger even when a round proposes nothing (re-chew regression)', async () => {
    // The original bug: a round that proposed 0 (or proposed only could rows) kept the
    // loop alive forever. The attempted ledger must stop it after one attempt per key.
    runReviewFleet.mockResolvedValue({ status: 'ready', accepted: true, proposed: 0 });
    const { result } = setup();
    await act(async () => { await result.current.handleReviewCool(); });
    expect(runReviewFleet).toHaveBeenCalledTimes(1); // round 1 attempts [A,B]; round 2 next=[] → stop
  });

  it('drains a foreign (prewarm) run on accepted:false, then re-runs OUR keys without marking them attempted', async () => {
    runReviewFleet
      .mockResolvedValueOnce({ status: 'running', accepted: false }) // foreign latch holder
      .mockResolvedValue({ status: 'ready', accepted: true, proposed: 1 }); // our pinned run
    const { result } = setup();
    await act(async () => { await result.current.handleReviewCool(); });
    expect(runReviewFleet).toHaveBeenCalledTimes(2);
    // the foreign drain did NOT consume our chunk → the 2nd call still pins [A,B]
    expect(runReviewFleet).toHaveBeenNthCalledWith(2, { itemKeys: ['A', 'B'] });
  });

  it('bounds back-to-back foreign runs (cannot spin forever)', async () => {
    runReviewFleet.mockResolvedValue({ status: 'running', accepted: false }); // always foreign
    const { result } = setup();
    await act(async () => { await result.current.handleReviewCool(); });
    // AUTO_REVIEW_MAX_DRAINS=5 drains, then the 6th call hits the cap and breaks.
    expect(runReviewFleet).toHaveBeenCalledTimes(6);
  });

  it('stops when no cool keys remain (empty queue → no fleet call)', async () => {
    fetchReadingQueue.mockResolvedValue({ items: [{ item_key: 'C', relevance_score: 2.8 }] });
    const { result } = setup();
    await act(async () => { await result.current.handleReviewCool(); });
    expect(runReviewFleet).not.toHaveBeenCalled();
  });

  it('stopReviewCool flips the bar into the honest "stopping" state', async () => {
    const { result } = setup();
    act(() => { result.current.stopReviewCool(); });
    expect(result.current.autoReview).toEqual({ active: false, stopping: true });
  });

  it('exposes the cool-undecided count from the queue prop', () => {
    const { result } = setup({ queue: COOL });
    expect(result.current.coolUndecided).toBe(2); // A + B; C is could_read
  });
});
