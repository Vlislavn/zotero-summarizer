// @vitest-environment jsdom
import { act, cleanup, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import NavBar from './NavBar.jsx';

vi.mock('../offlineStore.js', () => ({
  publishStatus: vi.fn(), resolveConflict: vi.fn(), syncStatusEvent: 'zs-sync-status',
}));
vi.mock('../syncClient.js', () => ({ syncNow: vi.fn() }));
afterEach(cleanup);

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
