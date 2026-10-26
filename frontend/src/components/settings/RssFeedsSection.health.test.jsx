// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const api = vi.hoisted(() => ({ addRssFeed: vi.fn(), deleteRssFeed: vi.fn(), fetchRssFeeds: vi.fn(),
  importRssFeedsFromZotero: vi.fn(), refreshRssFeeds: vi.fn(), updateRssFeed: vi.fn(),
  fetchDoctorStatus: vi.fn(), runDoctor: vi.fn(), cancelDoctor: vi.fn() }));
vi.mock('../../api/settingsApi.js', () => api);
vi.mock('../../api/setupApi.js', () => api);
import RssFeedsSection from './RssFeedsSection.jsx';
import { DoctorChecklist } from './DeploymentCard.jsx';

const feed = { id: 1, name: 'Synthetic source', url: 'https://example.test/rss', enabled: true };
const ready = { ready: true, status: 'ready', config_signature: 'sources-before', checks: [], modes: {} };
const changed = { ready: false, status: 'not_started', config_signature: 'sources-after', modes: {}, checks: [
  { id: 'rss_source', status: 'not_started', message: 'Source settings changed; verification required' },
] };
const missing = { ...changed, status: 'needs_action', checks: [
  { id: 'rss_source', status: 'needs_action', message: 'No RSS source is enabled',
    recovery: { action: 'open_sources_settings', label: 'Add a source in Settings' } },
] };
let feeds;
let health;
let client;

function mount() {
  client = new QueryClient({ defaultOptions: {
    queries: { retry: false, refetchOnWindowFocus: false }, mutations: { retry: false },
  } });
  return render(<QueryClientProvider client={client}>
    <DoctorChecklist /><RssFeedsSection />
  </QueryClientProvider>);
}

beforeEach(() => {
  Object.values(api).forEach((mock) => mock.mockReset());
  feeds = [{ ...feed }];
  health = ready;
  api.fetchRssFeeds.mockImplementation(async () => ({ feeds: [...feeds] }));
  api.fetchDoctorStatus.mockImplementation(async () => health);
  api.addRssFeed.mockImplementation(async (body) => {
    const saved = { ...body, id: 2 };
    feeds = [...feeds, saved];
    health = changed;
    return saved;
  });
  api.updateRssFeed.mockImplementation(async (id, body) => {
    feeds = feeds.map((row) => row.id === id ? { ...row, ...body } : row);
    health = changed;
    return { id, ...body };
  });
  api.deleteRssFeed.mockImplementation(async (id) => {
    feeds = feeds.filter((row) => row.id !== id);
    health = changed;
    return { status: 'deleted' };
  });
  api.importRssFeedsFromZotero.mockImplementation(async () => {
    feeds = [{ ...feed }];
    health = changed;
    return { imported: 1 };
  });
});
afterEach(() => { cleanup(); client?.clear(); });

it.each(['add', 'update', 'disable', 'delete', 'import'])('%s refreshes the shared Doctor status without running checks', async (action) => {
  if (action === 'add' || action === 'import') { feeds = []; health = missing; }
  mount();
  await waitFor(() => expect(api.fetchDoctorStatus).toHaveBeenCalledTimes(1));
  await waitFor(() => expect(api.fetchRssFeeds).toHaveBeenCalledTimes(1));
  if (action === 'add' || action === 'import') await screen.findByText('No RSS feeds saved.');
  else await screen.findByRole('button', { name: 'Save', exact: true });
  if (action === 'add') {
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'New source' } });
    fireEvent.change(screen.getByLabelText('URL'), { target: { value: 'https://example.test/new-rss' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add feed' }));
  } else if (action === 'import') {
    fireEvent.click(screen.getByRole('button', { name: 'Import feeds from Zotero' }));
  } else if (action === 'delete') {
    fireEvent.click(await screen.findByRole('button', { name: 'Delete', exact: true }));
  } else {
    if (action === 'disable') fireEvent.click(await screen.findByLabelText('Enabled'));
    else fireEvent.change(screen.getAllByLabelText('Name')[1], { target: { value: 'Renamed source' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save', exact: true }));
  }

  await screen.findByText('Source settings changed; verification required');
  expect(api.fetchDoctorStatus).toHaveBeenCalledTimes(2);
  expect(client.getQueryData(['setup-doctor'])).toEqual(changed);
  expect(screen.queryByText('✓ Setup verified')).toBeNull();
  expect(api.runDoctor).not.toHaveBeenCalled();
  expect(api.refreshRssFeeds).not.toHaveBeenCalled();
});

it('retires verification of a removed last source and recovers only after an explicit new check', async () => {
  let finishOld;
  const oldCheck = new Promise((resolve) => { finishOld = resolve; });
  api.runDoctor.mockReturnValueOnce(oldCheck).mockImplementationOnce(async () => {
    health = missing;
    return health;
  }).mockImplementationOnce(async () => {
    health = { ...ready, config_signature: 'recovered-sources' };
    return health;
  });
  mount();
  await screen.findByText('✓ Setup verified');
  fireEvent.click(screen.getByRole('button', { name: 'Verify setup' }));
  await waitFor(() => expect(api.runDoctor).toHaveBeenCalledTimes(1));
  fireEvent.click(await screen.findByRole('button', { name: 'Delete', exact: true }));
  await screen.findByText('No RSS feeds saved.');
  await screen.findByText('Source settings changed; verification required');

  await act(async () => { finishOld(ready); await oldCheck; });
  await waitFor(() => expect(screen.getByRole('button', { name: 'Verify setup' }).disabled).toBe(false));
  expect(client.getQueryData(['setup-doctor'])).toEqual(changed);
  expect(screen.queryByText('✓ Setup verified')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Verify setup' }));
  await screen.findByText('No RSS source is enabled');

  fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Recovery source' } });
  fireEvent.change(screen.getByLabelText('URL'), { target: { value: 'https://example.test/recovery' } });
  fireEvent.click(screen.getByRole('button', { name: 'Add feed' }));
  await screen.findByText('Source settings changed; verification required');
  expect(api.runDoctor).toHaveBeenCalledTimes(2);
  expect(screen.queryByText('✓ Setup verified')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Verify setup' }));
  await screen.findByText('✓ Setup verified');
  expect(client.getQueryData(['setup-doctor']).config_signature).toBe('recovered-sources');
  expect(api.runDoctor).toHaveBeenCalledTimes(3);
});

it('does not discard valid health when a source save fails', async () => {
  api.deleteRssFeed.mockRejectedValueOnce(Object.assign(new Error('Synthetic delete failed'), { status: 403 }));
  mount();
  await screen.findByText('✓ Setup verified');
  fireEvent.click(await screen.findByRole('button', { name: 'Delete', exact: true }));
  await screen.findByText('Synthetic delete failed');
  expect(client.getQueryData(['setup-doctor'])).toEqual(ready);
  expect(api.fetchDoctorStatus).toHaveBeenCalledTimes(1);
});

async function writeSource(action) {
  if (action === 'add') {
    fireEvent.change(screen.getAllByLabelText('Name')[0], { target: { value: 'New source' } });
    fireEvent.change(screen.getAllByLabelText('URL')[0], { target: { value: 'https://example.test/new-rss' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add feed' }));
  } else if (action === 'import') {
    fireEvent.click(screen.getByRole('button', { name: 'Import feeds from Zotero' }));
  } else if (action === 'delete') {
    fireEvent.click(screen.getByRole('button', { name: 'Delete', exact: true }));
  } else {
    if (action === 'disable') fireEvent.click(screen.getByLabelText('Enabled'));
    else fireEvent.change(screen.getAllByLabelText('Name')[1], { target: { value: 'Renamed source' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save', exact: true }));
  }
}

it.each(['add', 'update', 'disable', 'delete', 'import'].flatMap((action) =>
  ['transport', '5xx', 'JSON'].map((failure) => [action, failure])))(
  '%s rereads rows and Doctor after committed-write %s uncertainty without retrying', async (action, failure) => {
  const mutation = api[{
    add: 'addRssFeed', update: 'updateRssFeed', disable: 'updateRssFeed',
    delete: 'deleteRssFeed', import: 'importRssFeedsFromZotero',
  }[action]];
  if (action === 'import') mutation.mockImplementation(async () => {
    feeds = [{ ...feed, name: 'Imported source' }];
    health = changed;
    return { imported: 1 };
  });
  const commit = mutation.getMockImplementation();
  const error = failure === 'transport' ? new TypeError('Synthetic uncertain write')
    : failure === 'JSON' ? new SyntaxError('Synthetic uncertain write')
      : Object.assign(new Error('Synthetic uncertain write'), { status: 503 });
  mutation.mockImplementationOnce(async (...args) => { await commit(...args); throw error; });
  mount();
  await screen.findByText('✓ Setup verified');
  await screen.findByRole('button', { name: 'Save', exact: true });
  await writeSource(action);
  await screen.findByText('Synthetic uncertain write');
  await screen.findByText('Source settings changed; verification required');
  await waitFor(() => expect(client.getQueryData(['rss-feeds'])?.feeds).toEqual(feeds));
  expect(api.fetchRssFeeds).toHaveBeenCalledTimes(2);
  expect(api.fetchDoctorStatus).toHaveBeenCalledTimes(2);
  expect(client.getQueryData(['setup-doctor'])).toEqual(changed);
  expect(screen.queryByText('✓ Setup verified')).toBeNull();
  expect(mutation).toHaveBeenCalledTimes(1);
  expect(api.runDoctor).not.toHaveBeenCalled();
  expect(api.refreshRssFeeds).not.toHaveBeenCalled();
  if (action === 'delete') await screen.findByText('No RSS feeds saved.');
});
