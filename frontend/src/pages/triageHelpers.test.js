import { describe, expect, it } from 'vitest';
import {
  progressPercent,
  formatPercent,
  statusTone,
  isTerminalStatus,
} from './triageHelpers.js';

describe('progressPercent', () => {
  it('returns 0 for a null/undefined job', () => {
    expect(progressPercent(null)).toBe(0);
    expect(progressPercent(undefined)).toBe(0);
  });

  it('returns 0 when total is zero or missing (no divide-by-zero)', () => {
    expect(progressPercent({ completed: 5, total: 0 })).toBe(0);
    expect(progressPercent({ completed: 5 })).toBe(0);
  });

  it('rounds the completed/total ratio to a whole percent', () => {
    expect(progressPercent({ completed: 1, total: 3 })).toBe(33);
    expect(progressPercent({ completed: 2, total: 3 })).toBe(67);
  });

  it('clamps to 100 even if completed overshoots total', () => {
    expect(progressPercent({ completed: 12, total: 10 })).toBe(100);
  });

  it('floors negative completed at 0', () => {
    expect(progressPercent({ completed: -4, total: 10 })).toBe(0);
  });

  it('coerces string counts the API may send', () => {
    expect(progressPercent({ completed: '5', total: '10' })).toBe(50);
  });
});

describe('formatPercent', () => {
  it('formats a 0..1 ratio as a whole-percent string', () => {
    expect(formatPercent(0.5)).toBe('50%');
    expect(formatPercent(0.873)).toBe('87%');
    expect(formatPercent(0)).toBe('0%');
  });

  it('returns "n/a" for non-finite / non-numeric input', () => {
    expect(formatPercent(undefined)).toBe('n/a');
    expect(formatPercent('not-a-number')).toBe('n/a');
    expect(formatPercent(NaN)).toBe('n/a');
  });

  it('coerces null to 0% (Number(null) === 0), matching the original helper', () => {
    expect(formatPercent(null)).toBe('0%');
  });
});

describe('statusTone', () => {
  it('maps known statuses onto the shared tone vocabulary', () => {
    expect(statusTone('running')).toBe('teal');
    expect(statusTone('completed')).toBe('emerald');
    expect(statusTone('done')).toBe('emerald');
    expect(statusTone('failed')).toBe('rose');
    expect(statusTone('cancelled')).toBe('slate');
  });

  it('is case-insensitive', () => {
    expect(statusTone('RUNNING')).toBe('teal');
    expect(statusTone('Completed')).toBe('emerald');
  });

  it('falls back to slate for unknown / empty status', () => {
    expect(statusTone('whatever')).toBe('slate');
    expect(statusTone('')).toBe('slate');
    expect(statusTone(undefined)).toBe('slate');
  });
});

describe('isTerminalStatus', () => {
  it('is true for finished statuses', () => {
    expect(isTerminalStatus('completed')).toBe(true);
    expect(isTerminalStatus('failed')).toBe(true);
    expect(isTerminalStatus('cancelled')).toBe(true);
    expect(isTerminalStatus('DONE')).toBe(true);
  });

  it('is false while running or for unknown/empty status', () => {
    expect(isTerminalStatus('running')).toBe(false);
    expect(isTerminalStatus('queued')).toBe(false);
    expect(isTerminalStatus('')).toBe(false);
    expect(isTerminalStatus(undefined)).toBe(false);
  });
});
