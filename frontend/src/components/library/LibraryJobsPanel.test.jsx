// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import LibraryJobsPanel from './LibraryJobsPanel.jsx';

afterEach(cleanup);

const ctx = { modelReady: true, computedAt: new Date().toISOString(), scoresStale: false, unscored: 0 };
const ZOTERO = { pending: 0, syncing: false, fetching: false, syncedAt: { tags: '', ranks: '' }, onSend: vi.fn(), onFetch: vi.fn() };
const PICKS = { fleetStatus: null, onRun: vi.fn(), onStop: vi.fn(), coolCount: 0, proposedCount: 0 };
const show = (props = {}) => render(
  <MemoryRouter><LibraryJobsPanel ctx={ctx} onRescore={vi.fn()} zotero={ZOTERO} picks={PICKS} {...props} /></MemoryRouter>,
);
const open = () => fireEvent.click(screen.getByText('Library jobs'));

describe('LibraryJobsPanel', () => {
  it('is one closed disclosure whose sections are Scores and Zotero when nothing is left to review', () => {
    show();
    expect(screen.getByTestId('library-jobs').hasAttribute('open')).toBe(false);
    open();
    expect(screen.getByRole('region', { name: 'Scores' })).toBeTruthy();
    expect(screen.getByRole('region', { name: 'Zotero' })).toBeTruthy();
    expect(screen.queryByRole('region', { name: 'Top-pick reviews' })).toBeNull();  // hidden at 0
  });

  it('Scores: says how fresh they are and rescores on demand, also after a failed read', () => {
    const onRescore = vi.fn();
    show({ onRescore, ctx: { ...ctx, scoresStale: true, unscored: 4, err: { status: 500 } } });
    open();
    expect(screen.getByRole('region', { name: 'Scores' }).textContent).toMatch(/older model.*4 not scored/);
    fireEvent.click(screen.getByRole('button', { name: 'Rescore' }));
    expect(onRescore).toHaveBeenCalledTimes(1);
  });

  it('Scores: no Rescore while scoring runs or while the model is not ready', () => {
    show({ ctx: { ...ctx, computing: true } });
    expect(screen.getByRole('button', { name: 'Scoring…' }).disabled).toBe(true);
    cleanup();
    show({ ctx: { ...ctx, modelReady: false } });
    expect(screen.queryByRole('button', { name: /Rescore|Scoring/ })).toBeNull();
  });

  it('Zotero: sends, fetches PDFs and links the pending changes to Ops → Pending', () => {
    const onSend = vi.fn();
    const onFetch = vi.fn();
    show({ zotero: { ...ZOTERO, pending: 82, onSend, onFetch, syncedAt: { tags: '2026-11-12T15:02:00Z', ranks: '' } } });
    fireEvent.click(screen.getByRole('button', { name: 'Send to Zotero…' }));
    fireEvent.click(screen.getByRole('button', { name: 'Fetch missing PDFs…' }));
    expect(onSend).toHaveBeenCalledTimes(1);
    expect(onFetch).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('link', { name: '82 Zotero changes to apply ›' }).getAttribute('href')).toBe('/ops?tab=pending');
    expect(screen.getByRole('region', { name: 'Zotero' }).textContent).toMatch(/tags: 2026-11-12 · Call-Number ranks: never/);
  });

  it('Zotero: no section without a Zotero connection; a running send locks both buttons', () => {
    show({ zotero: null });
    expect(screen.queryByRole('region', { name: 'Zotero' })).toBeNull();
    cleanup();
    show({ zotero: { ...ZOTERO, syncing: true } });
    expect(screen.getByRole('button', { name: 'Sending…' }).disabled).toBe(true);
    expect(screen.getByRole('button', { name: 'Fetch missing PDFs…' }).disabled).toBe(true);
  });

  it('Top-pick reviews: shown with the run button while picks are undecided; a running review opens the panel', () => {
    const { rerender } = show({ picks: { ...PICKS, coolCount: 3 } });
    expect(screen.getByRole('button', { name: 'Deep-review top picks (3)' })).toBeTruthy();
    expect(screen.getByTestId('library-jobs').hasAttribute('open')).toBe(false);
    rerender(<MemoryRouter><LibraryJobsPanel ctx={ctx} onRescore={vi.fn()} zotero={ZOTERO} picks={{ ...PICKS, coolCount: 3, autoActive: true }} /></MemoryRouter>);
    expect(screen.getByTestId('library-jobs').hasAttribute('open')).toBe(true);
    expect(screen.getByRole('button', { name: 'Stop' })).toBeTruthy();
  });

  it('Top-pick reviews: keeps a finished run’s outcome, without the retired «rows below» card wording', () => {
    show({ picks: { ...PICKS, fleetStatus: { status: 'ready', proposed: 2 } } });
    const text = screen.getByRole('region', { name: 'Top-pick reviews' }).textContent;
    expect(text).toMatch(/Reviewed and proposed 2 verdicts/);
    expect(text).not.toMatch(/Confirm|Override/);
  });

  it('offline: every job is disabled and the reason is written', () => {
    show({ unavailable: 'Jobs need the Mac (offline)', picks: { ...PICKS, coolCount: 2 } });
    expect(screen.getByText('Jobs need the Mac (offline)')).toBeTruthy();
    for (const name of ['Rescore', 'Send to Zotero…', 'Fetch missing PDFs…', 'Deep-review top picks (2)']) {
      expect(screen.getByRole('button', { name }).closest('fieldset').disabled).toBe(true);
    }
  });
});
