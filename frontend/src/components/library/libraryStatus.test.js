import { describe, expect, it } from 'vitest';
import { emptyState, libraryStatus } from './libraryStatus.js';

const NOW = new Date().toISOString();
const ok = { connection: 'ok', rows: 40, modelReady: true, computedAt: NOW, scoresStale: false, unscored: 0, pending: 0 };

// One row per priority of the status line: the situation, the id that must win, the one action it offers.
// Each row leaves every lower-priority trigger switched on, so it also proves the order.
const LOWER = { computing: true, receipt: { text: 'Sent to Zotero' }, scoresStale: true, unscored: 9, pending: 3 };
const PRIORITIES = [
  ['1 offline', { ...LOWER, offline: true, err: { status: 500 }, scoringError: 'x' }, 'offline', 'Sync now'],
  ['2 load error 503', { ...LOWER, err: { status: 503 }, scoringError: 'x' }, 'error_busy', 'Retry'],
  ['2 load error 422', { ...LOWER, err: { status: 422 } }, 'error_stale_app', 'Reload'],
  ['2 load error other', { ...LOWER, err: Object.assign(new Error('boom'), { status: 500 }) }, 'error_load', 'Retry'],
  ['3 scoring failed', { ...LOWER, scoringError: 'model crashed' }, 'scoring_failed', 'Rescore'],
  ['4 connecting', { ...LOWER, connection: 'checking' }, 'connecting', null],
  ['4 slow', { ...LOWER, connection: 'busy' }, 'slow', 'Sync now'],
  ['5 scoring runs', LOWER, 'computing', null],
  ['6 receipt', { ...LOWER, computing: false }, 'receipt', 'Dismiss'],
  ['7 summary', { ...LOWER, computing: false, receipt: null }, 'summary', 'Review changes ›'],
  ['8 up to date', {}, 'up_to_date', null],
];

describe('libraryStatus: the first of eight priorities wins, with one sentence and at most one action', () => {
  it.each(PRIORITIES)('%s', (_name, patch, id, action) => {
    const message = libraryStatus({ ...ok, ...patch });
    expect(message.id).toBe(id);
    expect(message.text.length).toBeGreaterThan(0);
    expect(message.action?.label ?? null).toBe(action);
  });

  it('walks down the priorities as each trigger clears', () => {
    const ctx = { ...ok, ...LOWER, offline: true, err: { status: 500 }, scoringError: 'x' };
    const seen = [libraryStatus(ctx).id];
    for (const clear of [{ offline: false }, { err: null }, { scoringError: null }, { computing: false }, { receipt: null }, { scoresStale: false, unscored: 0, pending: 0 }]) {
      Object.assign(ctx, clear);
      seen.push(libraryStatus(ctx).id);
    }
    expect(seen).toEqual(['offline', 'error_load', 'scoring_failed', 'computing', 'receipt', 'summary', 'up_to_date']);
  });

  it('is neutral «Connecting to your Mac…» while the first request is out over the device copy (cold cache)', () => {
    for (const patch of [{ loading: true, offline: true, connection: 'mac' }, { loading: true, offline: true, connection: 'ok' }]) {
      const message = libraryStatus({ ...ok, ...patch });
      expect(message).toMatchObject({ id: 'connecting', tone: 'info', text: 'Connecting to your Mac…', action: null });
    }
  });

  it('says nothing true while the very first answer is out and there is nothing to show', () => {
    expect(libraryStatus({ ...ok, loading: true, rows: 0 })).toBeNull();
  });

  it('keeps a plain summary while a re-read runs over rows already on screen', () => {
    expect(libraryStatus({ ...ok, loading: true, rows: 40 }).id).toBe('up_to_date');
  });

  it('puts severity into the tone: load errors and a failed scoring are errors; offline, busy and slow are warnings', () => {
    const tone = (patch) => libraryStatus({ ...ok, ...patch }).tone;
    expect(tone({ err: { status: 422 } })).toBe('error');
    expect(tone({ err: { status: 500 } })).toBe('error');
    expect(tone({ scoringError: 'x' })).toBe('error');
    expect(tone({ err: { status: 503 } })).toBe('warning');
    expect(tone({ offline: true })).toBe('warning');
    expect(tone({ connection: 'busy' })).toBe('warning');
    expect(tone({ computing: true })).toBe('info');
  });

  it('keeps the list in view in the sentence: last loaded list on an error, N papers saved offline', () => {
    expect(libraryStatus({ ...ok, err: { status: 500 } }).text).toMatch(/Showing the last loaded list/);
    expect(libraryStatus({ ...ok, rows: 0, err: { status: 500 } }).text).not.toMatch(/last loaded list/);
    expect(libraryStatus({ ...ok, offline: true, rows: 1 }).text).toMatch(/showing 1 paper saved on this device/);
    expect(libraryStatus({ ...ok, offline: true, rows: 0 }).text).toMatch(/no papers are saved/);
  });

  it('summarises scores, not-scored papers and Zotero changes in one sentence', () => {
    const message = libraryStatus({ ...ok, computedAt: '2020-01-01T00:00:00Z', scoresStale: true, unscored: 231, pending: 82 });
    expect(message.text).toMatch(/^Scores updated .* \(older model\) · 231 not scored · 82 Zotero changes to apply$/);
    expect(message.action).toMatchObject({ label: 'Review changes ›', to: '/ops?tab=pending' });
  });

  it('never claims up to date when the model is not ready or nothing is scored', () => {
    expect(libraryStatus({ ...ok, modelReady: false }).id).toBe('summary');
    expect(libraryStatus({ ...ok, computedAt: null }).text).toBe('Papers are not scored yet');
  });
});

const list = { rows: 0, rawCount: 0, modelReady: true, computedAt: NOW, readHidden: 0 };
const EMPTY = [
  ['filters cut every row', { rawCount: 80, hasActiveFilters: true }, 'filters', 'Clear filters'],
  ['search finds nothing', { search: 'agents' }, 'search', 'Clear search'],
  ['collection has nothing unread', { scopeName: 'Inbox' }, 'scope', 'Show all items'],
  ['nothing scored yet', { computedAt: null }, 'not_scored', 'Rescore'],
  ['all caught up', { readHidden: 12 }, 'caught_up', 'Show already-read'],
  ['no papers at all', {}, 'empty', 'Open Today'],
];

describe('emptyState: six empty lists, each one sentence and one action', () => {
  it.each(EMPTY)('%s', (_name, patch, id, action) => {
    const state = emptyState({ ...list, ...patch });
    expect(state.id).toBe(id);
    expect(state.text.length).toBeGreaterThan(0);
    expect(state.action.label).toBe(action);
  });

  it('words the collection and search cases with the names', () => {
    expect(emptyState({ ...list, scopeName: 'Inbox' }).text).toBe('Nothing unread in “Inbox”.');
    expect(emptyState({ ...list, search: 'agents', scopeName: 'Inbox' }).text).toBe('No papers match “agents” in “Inbox”.');
    expect(emptyState({ ...list, readHidden: 1 }).text).toMatch(/1 read paper hidden/);
  });

  it.each([
    ['loading', { loading: true }], ['a failed read', { err: { status: 500 } }], ['offline', { offline: true }],
    ['scoring', { computing: true }], ['a failed scoring', { scoringError: 'x' }], ['rows on screen', { rows: 3 }],
  ])('shows no empty list during %s', (_name, patch) => {
    expect(emptyState({ ...list, readHidden: 5, ...patch })).toBeNull();
  });
});
