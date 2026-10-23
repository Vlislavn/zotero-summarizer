// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import RelatedPapers from './RelatedPapers.jsx';
import { fetchRelatedPapers, assessRelatedPapers } from '../../api/libraryApi.js';

vi.mock('../../api/libraryApi.js', () => ({ fetchRelatedPapers: vi.fn(), assessRelatedPapers: vi.fn(),
  fetchDiscovery: async () => ({ admission: { state: 'unknown', basis: null }, context_fingerprint: null }),
  findNewRelevant: vi.fn(),
}));
afterEach(() => { cleanup(); vi.resetAllMocks(); });
const old = { status: 'ready', context_fingerprint: 'old', comments_in_ranking: false,
  boundary: { enabled: false }, items: [], seed_comments: { availability: 'available', app_state: 'observed',
    native_state: 'empty', records: [{ id: 'old-comment', text: 'OBSOLETE PRIVATE EXCERPT', source: 'app_note', authorship: 'human' }] } };

function setup() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const tree = revision => <QueryClientProvider client={client}><MemoryRouter>
    <RelatedPapers key={revision} itemKey="SEED0001" />
  </MemoryRouter></QueryClientProvider>;
  const view = render(tree('before-save'));
  return { client, remount: revision => view.rerender(tree(revision)) };
}

it.each(['edited', 'deleted'])('hides cached old seed context after a saved note is %s', async change => {
  fetchRelatedPapers.mockResolvedValue(old);
  const { client, remount } = setup();
  fireEvent.click(screen.getByRole('button', { name: 'Show related saved papers' }));
  await screen.findByText('OBSOLETE PRIVATE EXCERPT');

  remount(`note-${change}`);

  expect(screen.queryByText('OBSOLETE PRIVATE EXCERPT')).toBeNull();
  expect(assessRelatedPapers).not.toHaveBeenCalled();
  client.clear();
});

it('an old pending GET cannot republish pre-save context after the remount', async () => {
  let finish;
  fetchRelatedPapers.mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  const { client, remount } = setup();
  fireEvent.click(screen.getByRole('button', { name: 'Show related saved papers' }));
  await waitFor(() => expect(fetchRelatedPapers).toHaveBeenCalledOnce());
  remount('after-note-deletion');

  await act(async () => { finish(old); });
  await waitFor(() => expect(client.isFetching({ queryKey: ['related-papers', 'SEED0001'] })).toBe(0));

  expect(screen.queryByText('OBSOLETE PRIVATE EXCERPT')).toBeNull();
  expect(assessRelatedPapers).not.toHaveBeenCalled();
  client.clear();
});

it.each(['edited', 'deleted'])('explicit reload after %s reads fresh context without reusing an old pending GET', async change => {
  let finishOld;
  const fresh = { ...old, context_fingerprint: 'fresh', seed_comments: { ...old.seed_comments,
    availability: change === 'deleted' ? 'empty' : 'available',
    records: change === 'deleted' ? [] : [{ ...old.seed_comments.records[0], text: 'CURRENT SAVED EXCERPT' }] } };
  fetchRelatedPapers.mockImplementationOnce(() => new Promise(resolve => { finishOld = resolve; }))
    .mockResolvedValueOnce(fresh);
  const { client, remount } = setup();
  fireEvent.click(screen.getByRole('button', { name: 'Show related saved papers' }));
  await waitFor(() => expect(fetchRelatedPapers).toHaveBeenCalledOnce());
  const oldSignal = fetchRelatedPapers.mock.calls[0][1].signal;

  remount(`saved-${change}`);
  expect(oldSignal.aborted).toBe(true);
  expect(screen.queryByText('OBSOLETE PRIVATE EXCERPT')).toBeNull();
  expect(fetchRelatedPapers).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByRole('button', { name: 'Show related saved papers' }));
  await waitFor(() => expect(fetchRelatedPapers).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(screen.queryByText('Loading saved-library connections…')).toBeNull());
  if (change === 'edited') expect(screen.getByText('CURRENT SAVED EXCERPT')).toBeTruthy();
  else expect(screen.getByText('Comments · empty')).toBeTruthy();

  await act(async () => { finishOld(old); });

  expect(screen.queryByText('OBSOLETE PRIVATE EXCERPT')).toBeNull();
  if (change === 'edited') expect(screen.getByText('CURRENT SAVED EXCERPT')).toBeTruthy();
  expect(assessRelatedPapers).not.toHaveBeenCalled();
  client.clear();
});

it('a fresh read failure after a save is an error, never old comments or observed empty', async () => {
  fetchRelatedPapers.mockResolvedValueOnce(old).mockRejectedValueOnce(new Error('synthetic read failed'))
    .mockResolvedValueOnce({ ...old, context_fingerprint: 'recovered', seed_comments: { ...old.seed_comments,
      records: [{ ...old.seed_comments.records[0], text: 'RECOVERED CURRENT EXCERPT' }] } });
  const { client, remount } = setup();
  fireEvent.click(screen.getByRole('button', { name: 'Show related saved papers' }));
  await screen.findByText('OBSOLETE PRIVATE EXCERPT');
  remount('saved-context');
  fireEvent.click(screen.getByRole('button', { name: 'Show related saved papers' }));
  await screen.findByRole('alert');
  expect(screen.queryByText('OBSOLETE PRIVATE EXCERPT')).toBeNull();
  expect(screen.queryByText('Comments · empty')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Retry library read' }));
  await screen.findByText('RECOVERED CURRENT EXCERPT');
  expect(screen.queryByText('OBSOLETE PRIVATE EXCERPT')).toBeNull();
  expect(assessRelatedPapers).not.toHaveBeenCalled();
  client.clear();
});
