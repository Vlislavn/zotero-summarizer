// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import LibraryStatusLine, { LibraryEmptyState } from './LibraryStatusLine.jsx';

afterEach(() => { cleanup(); vi.useRealTimers(); });

const ctx = { connection: 'ok', rows: 5, modelReady: true, computedAt: new Date().toISOString(), unscored: 0, pending: 0 };
const show = (patch, handlers = {}) => render(
  <MemoryRouter><LibraryStatusLine ctx={{ ...ctx, ...patch }} handlers={handlers} /></MemoryRouter>,
);

describe('LibraryStatusLine', () => {
  it('announces an error at once and names it an error in words', () => {
    show({ err: { status: 500 } });
    const line = screen.getByRole('alert');
    expect(line.textContent).toMatch(/^Error:\s*Couldn’t load your list/);
    expect(screen.queryByRole('status')).toBeNull();
  });

  it('keeps everything else a polite status; a warning says «Warning», a neutral line says no severity', () => {
    show({ err: { status: 503 } });
    expect(screen.queryByRole('alert')).toBeNull();
    expect(screen.getByRole('status').textContent).toMatch(/^Warning:\s*Zotero is busy/);
    cleanup();
    show({ computing: true });
    expect(screen.getByRole('status').textContent).not.toMatch(/Warning|Error/);
  });

  it('runs the one action of the message', () => {
    const retry = vi.fn();
    show({ err: { status: 500 } }, { retry });
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(retry).toHaveBeenCalledTimes(1);
  });

  it('links «Zotero changes to apply» to Ops → Pending', () => {
    show({ pending: 82 });
    expect(screen.getByRole('link', { name: 'Review changes ›' }).getAttribute('href')).toBe('/ops?tab=pending');
  });

  it('stays neutral «Connecting to your Mac…» for 5 s, then says the app is slow', () => {
    vi.useFakeTimers();
    show({ connection: 'checking' });
    expect(screen.getByRole('status').textContent).toBe('Connecting to your Mac…');
    act(() => { vi.advanceTimersByTime(5000); });
    expect(screen.getByRole('status').textContent).toMatch(/^Warning:\s*The app is slow to answer/);
    expect(screen.getByRole('button', { name: 'Sync now' })).toBeTruthy();
  });

  it('renders nothing while there is nothing true to say', () => {
    show({ loading: true, rows: 0 });
    expect(screen.queryByTestId('library-status')).toBeNull();
  });
});

describe('LibraryEmptyState', () => {
  it('shows its sentence with its one action, and nothing without a state', () => {
    const clearSearch = vi.fn();
    const { rerender } = render(
      <MemoryRouter><LibraryEmptyState state={{ id: 'search', text: 'No papers match “x”.', action: { id: 'clearSearch', label: 'Clear search' } }} handlers={{ clearSearch }} /></MemoryRouter>,
    );
    expect(screen.getByRole('status').textContent).toContain('No papers match “x”.');
    fireEvent.click(screen.getByRole('button', { name: 'Clear search' }));
    expect(clearSearch).toHaveBeenCalledTimes(1);
    rerender(<MemoryRouter><LibraryEmptyState state={null} handlers={{}} /></MemoryRouter>);
    expect(screen.queryByTestId('library-empty')).toBeNull();
  });
});
