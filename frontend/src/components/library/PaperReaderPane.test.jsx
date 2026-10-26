// @vitest-environment jsdom
import 'fake-indexeddb/auto';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import PaperReaderPane from './PaperReaderPane.jsx';
import { buildPaperRender, fetchPaperRender } from '../../api/libraryApi.js';
import { publishStatus } from '../../offlineStore.js';

vi.mock('../../api/libraryApi.js', async (importOriginal) => ({
  ...await importOriginal(),
  fetchPaperRender: vi.fn(),
  buildPaperRender: vi.fn(),
}));

const clients = [];
beforeEach(async () => {
  vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(true);
  await publishStatus('', 'connected');
  buildPaperRender.mockResolvedValue({ status: 'running' });
});
afterEach(async () => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
  vi.clearAllMocks();
  vi.restoreAllMocks();
  await publishStatus('', 'unchecked');
});

function show(state, itemKey = 'KEY') {
  fetchPaperRender.mockResolvedValue(state);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  clients.push(client);
  return { client, ...render(
    <QueryClientProvider client={client}>
      <PaperReaderPane itemKey={itemKey} open />
    </QueryClientProvider>,
  ) };
}

it.each(['missing', 'running'])('describes only published outputs while %s', async (status) => {
  show({ status });
  const notice = await screen.findByText(status === 'missing' ? /No paper brief yet/ : /Building .*audit/);
  expect(notice.textContent).not.toMatch(/notes/i);
  expect(notice.textContent).toMatch(/figures/);
  expect(notice.textContent).toMatch(/audit/);
});

it('shows the audit failure without exposing old artifact links or figures', async () => {
  show({
    status: 'error', error: 'paper_audit_failed', message: 'Paper audit is blocking; rebuild.',
    figures: [{ name: 'fig1_old.png', caption: 'Old result' }],
  });
  expect(await screen.findByText(/Build failed: Paper audit is blocking/)).toBeTruthy();
  expect(screen.queryAllByRole('link')).toHaveLength(0);
  expect(screen.queryAllByRole('img')).toHaveLength(0);
  expect(screen.getByRole('button', { name: 'Build paper brief' })).toBeTruthy();
});

it('withholds retained native artifacts when a confirmed connection becomes unavailable', async () => {
  show({ status: 'completed', audit: { status: 'passed', blocking: [] },
    figures: [{ name: 'fig1_approved.png', caption: 'Approved result' }] });
  await screen.findByRole('link', { name: /Open static brief/ });
  await act(async () => { await publishStatus('Owned transport failure', 'unavailable'); });
  expect(screen.getByText(/Figures & full brief unavailable/)).toBeTruthy();
  expect(screen.queryAllByRole('link')).toHaveLength(0);
  expect(screen.queryAllByRole('img')).toHaveLength(0);
  expect(fetchPaperRender).toHaveBeenCalledTimes(1);
});

it('withholds stale completed outputs while retaining the notice and rebuild action', async () => {
  buildPaperRender.mockRejectedValue(new Error('Owned build refusal'));
  show({
    status: 'completed', stale: true, pdf_key: 'old-source', built_at: 'publication-1',
    figures: [{ name: 'fig1_old.png', caption: 'Old result' }],
  });
  expect(await screen.findByText(/this brief is out of date/)).toBeTruthy();
  expect(await screen.findByText(/Build start failed: Owned build refusal/)).toBeTruthy();
  expect(screen.queryAllByRole('link')).toHaveLength(0);
  expect(screen.queryAllByRole('img')).toHaveLength(0);
  expect(screen.getByRole('button', { name: 'Rebuild' }).disabled).toBe(false);
  expect(buildPaperRender).toHaveBeenCalledTimes(1);
  expect(buildPaperRender).toHaveBeenCalledWith('KEY', {
    force: true, allowArxivSource: false, allowAcquireMissing: false,
  });
});

it('reports a missing listed output without showing the retained gallery', async () => {
  buildPaperRender.mockRejectedValue(new Error('Owned build refusal'));
  show({
    status: 'missing', stale: true, pdf_key: 'source-A', built_at: 'publication-1',
    message: 'A listed figure is missing; rebuild the artifact.',
    figures: [{ name: 'fig1_missing.png', caption: 'Missing result' }],
  });
  expect(await screen.findByText(/A listed figure is missing/)).toBeTruthy();
  expect(screen.queryAllByRole('link')).toHaveLength(0);
  expect(screen.queryAllByRole('img')).toHaveLength(0);
  expect(await screen.findByText(/Build start failed: Owned build refusal/)).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Build paper brief' }).disabled).toBe(false);
});

it('bounds automatic attempts by item and publication across cached pane reuse and failures', async () => {
  buildPaperRender.mockRejectedValue(new Error('Owned build refusal'));
  const states = {
    A: { status: 'completed', stale: true, pdf_key: 'source-A', built_at: 'publication-1' },
    B: { status: 'completed', stale: true, pdf_key: 'source-A', built_at: 'publication-1' },
  };
  const { client, rerender } = show(states.A, 'A');
  fetchPaperRender.mockImplementation(async (key) => states[key]);
  await screen.findByText(/Build start failed: Owned build refusal/);
  expect(buildPaperRender).toHaveBeenCalledTimes(1);
  await act(async () => { client.setQueryData(['paper-render', 'B'], states.B); });
  const visit = (key) => rerender(
    <QueryClientProvider client={client}>
      <PaperReaderPane itemKey={key} open />
    </QueryClientProvider>,
  );

  visit('B');
  await waitFor(() => expect(buildPaperRender).toHaveBeenCalledTimes(2));
  await screen.findByText(/Build start failed: Owned build refusal/);
  expect(buildPaperRender.mock.calls[1][0]).toBe('B');
  visit('A');
  await act(async () => { await client.refetchQueries({ queryKey: ['paper-render', 'A'] }); });
  expect(buildPaperRender).toHaveBeenCalledTimes(2);

  states.A = { ...states.A, built_at: 'publication-2' };
  await act(async () => { client.setQueryData(['paper-render', 'A'], states.A); });
  await waitFor(() => expect(buildPaperRender).toHaveBeenCalledTimes(3));
  await screen.findByText(/Build start failed: Owned build refusal/);
  await act(async () => { await client.refetchQueries({ queryKey: ['paper-render', 'A'] }); });
  expect(buildPaperRender).toHaveBeenCalledTimes(3);
});

it('publishes a held build result only to its original item query after cached navigation', async () => {
  let finish;
  buildPaperRender.mockReturnValue(new Promise(resolve => { finish = resolve; }));
  const original = { status: 'completed', stale: true, pdf_key: 'source-A', built_at: 'one' };
  const current = { status: 'completed', stale: false, pdf_key: 'source-B', built_at: 'two' };
  const { client, rerender } = show(original, 'A');
  await waitFor(() => expect(buildPaperRender).toHaveBeenCalledTimes(1));
  fetchPaperRender.mockImplementation(async key => key === 'B' ? current : original);
  await act(async () => { client.setQueryData(['paper-render', 'B'], current); });
  const invalidate = vi.spyOn(client, 'invalidateQueries');
  rerender(<QueryClientProvider client={client}><PaperReaderPane itemKey="B" open /></QueryClientProvider>);
  await screen.findByRole('link', { name: /Open static brief/ });
  await act(async () => { finish({ status: 'running' }); });
  await waitFor(() => expect(invalidate).toHaveBeenCalledWith({ queryKey: ['paper-render', 'A'] }));
  expect(invalidate).not.toHaveBeenCalledWith({ queryKey: ['paper-render', 'B'] });
});

it('keeps approved figures and brief links available', async () => {
  show({
    status: 'completed', audit: { status: 'passed', blocking: [] },
    figures: [{ name: 'fig1_approved.png', caption: 'Approved result' }],
  });
  expect(await screen.findByRole('link', { name: /Open static brief/ })).toBeTruthy();
  expect(screen.getByRole('img', { name: 'Approved result' })).toBeTruthy();
  expect(screen.getByRole('link', { name: /Open PDF used/ })).toBeTruthy();
});
