// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import NavBar from './NavBar.jsx';
import { resolveConflict } from '../offlineStore.js';
import { syncNow } from '../syncClient.js';

vi.mock('../offlineStore.js', () => ({
  publishStatus: vi.fn(), resolveConflict: vi.fn(), syncStatusEvent: 'zs-sync-status',
}));
vi.mock('../syncClient.js', () => ({ syncNow: vi.fn() }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });

it('makes the weekly digest reachable from primary navigation', () => {
  render(<MemoryRouter><NavBar /></MemoryRouter>);
  expect(screen.getByRole('link', { name: 'Weekly' }).getAttribute('href')).toBe('/weekly');
});

it('does not claim a confirmed sync before evidence and offers an explicit retry', () => {
  render(<MemoryRouter><NavBar /></MemoryRouter>);
  expect(screen.getByRole('status').textContent).toMatch(/Connection not confirmed/);
  expect(screen.queryByText('Synced')).toBeNull();
  act(() => window.dispatchEvent(new CustomEvent('zs-sync-status', { detail: {
    online: true, connection: 'unavailable', pending: 2, conflicts: [], rejected: [],
    sync_error: 'Server unavailable', paper_count: 3,
  } })));
  expect(screen.getByText(/2 pending/)).toBeTruthy();
  expect(screen.getByText(/Server unavailable/)).toBeTruthy();
  expect(screen.getByText(/Start the Mac app at this same address/)).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Sync now' }));
  expect(syncNow).toHaveBeenCalledOnce();
});

it('shows both conflict values and comments, and persists the explicit chosen resolution', async () => {
  render(<MemoryRouter><NavBar /></MemoryRouter>);
  act(() => window.dispatchEvent(new CustomEvent('zs-sync-status', { detail: {
    online: false, pending: 0, rejected: [], conflicts: [{
      mutation_id: 'conflict-uuid', item_key: 'P1', field: 'verdict', operation: 'set',
      value: 'dont_read', comment: 'Weak validation',
      canonical: { value: 'must_read', comment: 'Strong method' },
    }],
  } })));
  expect(screen.getByLabelText('Server').value).toBe('Must read\n\nComment:\nStrong method');
  expect(screen.getByLabelText('This device').value).toBe('Remove ❌\n\nComment:\nWeak validation');
  expect(screen.getByLabelText('Server').rows).toBe(4);
  expect(screen.getByLabelText('This device').rows).toBe(4);
  expect(screen.getByRole('button', { name: 'Sync now' }).disabled).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: 'Keep mine' }));
  await waitFor(() => expect(resolveConflict).toHaveBeenCalledWith('conflict-uuid', true));
  await waitFor(() => expect(syncNow).toHaveBeenCalledOnce());
});

it('surfaces a failed conflict save without claiming resolution', async () => {
  resolveConflict.mockRejectedValueOnce(new Error('Storage denied'));
  render(<MemoryRouter><NavBar /></MemoryRouter>);
  act(() => window.dispatchEvent(new CustomEvent('zs-sync-status', { detail: {
    online: true, pending: 0, rejected: [], conflicts: [{
      mutation_id: 'note-uuid', item_key: 'P2', field: 'review_note',
      value: 'My exact note', canonical: { value: 'Remote exact note' },
    }],
  } })));
  fireEvent.click(screen.getByRole('button', { name: 'Use server' }));
  expect(await screen.findByRole('alert')).toHaveProperty('textContent', expect.stringContaining('Resolution not saved: Storage denied'));
  expect(screen.getByLabelText('This device').value).toBe('My exact note');
  expect(syncNow).not.toHaveBeenCalled();
});

it('makes deployment limits and installation prerequisites visible without exposing a LAN setup', () => {
  render(<MemoryRouter><NavBar /></MemoryRouter>);
  fireEvent.click(screen.getByText('Offline use & installation'));
  expect(screen.getByText(/iPhone\/iPad Safari/)).toBeTruthy();
  expect(screen.getByText(/current server accepts same-machine loopback only/)).toBeTruthy();
  expect(screen.getByText(/An app sync acknowledgement does not prove Zotero/)).toBeTruthy();
});

it('wraps long stable keys in both rejected drafts and conflicts on mobile', () => {
  const key = `feed:g:${'b'.repeat(64)}`;
  render(<MemoryRouter><NavBar /></MemoryRouter>);
  expect(screen.getByRole('navigation').className).toContain('flex-wrap');
  expect(screen.getByRole('navigation').className).toContain('max-w-full');
  act(() => window.dispatchEvent(new CustomEvent('zs-sync-status', { detail: {
    online: true, pending: 0, conflicts: [], message: '', rejected: [
      { mutation_id: 'one', item_key: key, field: 'verdict', value: 'must_read', error: 'review_required' },
    ],
  } })));
  expect(screen.getByText((_, node) => node.tagName === 'P' && node.textContent.startsWith(key)).className)
    .toContain('break-words');
  act(() => window.dispatchEvent(new CustomEvent('zs-sync-status', { detail: {
    online: true, pending: 0, rejected: [], message: '', conflicts: [
      { mutation_id: 'two', item_key: key, field: 'verdict' },
    ],
  } })));
  expect(screen.getByText((_, node) => node.tagName === 'SPAN'
    && node.textContent.startsWith('Sync conflict for')).className).toContain('break-words');
});

it('exposes every rejected draft for recovery without pretending refresh repairs it', () => {
  render(<MemoryRouter><NavBar /></MemoryRouter>);
  act(() => window.dispatchEvent(new CustomEvent('zs-sync-status', { detail: {
    online: true, pending: 0, conflicts: [], message: '', rejected: [
      { mutation_id: 'one', item_key: 'P1', field: 'review_note', value: '<draft>\nmy note', error: 'Too long' },
      { mutation_id: 'two', item_key: 'P2', field: 'verdict', value: 'must_read', comment: 'my reason', error: 'Too long' },
    ],
  } })));
  const drafts = screen.getAllByLabelText(/Saved draft/);
  expect(drafts.map((field) => field.value)).toEqual(['<draft>\nmy note', 'must_read\n\nComment:\nmy reason']);
  expect(drafts.every((field) => field.readOnly)).toBe(true);
  expect(screen.getAllByRole('link', { name: 'Open paper', hidden: true }).map((link) => link.getAttribute('href')))
    .toEqual(['/paper/P1', '/paper/P2']);
  expect(screen.queryByText(/refresh the app before retrying/)).toBeNull();
});
