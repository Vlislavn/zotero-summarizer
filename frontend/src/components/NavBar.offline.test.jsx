// @vitest-environment jsdom
import 'fake-indexeddb/auto';
import { IDBFactory } from 'fake-indexeddb';
import { cleanup, render, screen, act, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

beforeEach(() => {
  vi.resetModules();
  vi.stubGlobal('indexedDB', new IDBFactory());
  Object.defineProperty(navigator, 'onLine', { configurable: true, value: true });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it('does not claim Synced before the first acknowledged pull', async () => {
  const { default: NavBar } = await import('./NavBar.jsx');
  await act(async () => { render(<MemoryRouter><NavBar /></MemoryRouter>); });
  expect(screen.queryByText('Synced', { exact: true })).toBeNull();
  await waitFor(() => expect(screen.getByRole('status').textContent).toBe('Connection not confirmed · 0 pending · 0 cached papers'));
});

it('shows actual server and device intent before either conflict choice', async () => {
  const offline = await import('../offlineStore.js');
  await offline.queueMutation({ item_key: 'P1', field: 'verdict', value: 'could_read', comment: 'Device judgement' });
  const [mutation] = await offline.pendingMutations();
  await offline.applyPushResults([{ mutation_id: mutation.mutation_id, status: 'conflict', conflict_revision: 8,
    canonical: { value: 'must_read', comment: 'Server judgement' } }]);
  const { default: NavBar } = await import('./NavBar.jsx');
  await act(async () => { render(<MemoryRouter><NavBar /></MemoryRouter>); });
  expect((await screen.findByRole('textbox', { name: 'Server', exact: true })).value).toBe('Must read\n\nComment:\nServer judgement');
  expect(screen.getByRole('textbox', { name: 'This device', exact: true }).value).toBe('Could read\n\nComment:\nDevice judgement');
  expect(screen.getByRole('button', { name: 'Keep mine', exact: true })).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Use server', exact: true })).toBeTruthy();
});
