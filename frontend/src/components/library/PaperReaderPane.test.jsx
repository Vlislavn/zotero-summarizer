// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import PaperReaderPane from './PaperReaderPane.jsx';
import { fetchPaperRender } from '../../api/libraryApi.js';

vi.mock('../../api/libraryApi.js', async (importOriginal) => ({
  ...await importOriginal(),
  fetchPaperRender: vi.fn(),
}));

afterEach(() => { cleanup(); vi.clearAllMocks(); });

function show(state) {
  fetchPaperRender.mockResolvedValue(state);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <PaperReaderPane itemKey="KEY" open />
    </QueryClientProvider>,
  );
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

it('keeps approved figures and brief links available', async () => {
  show({
    status: 'completed', audit: { status: 'passed', blocking: [] },
    figures: [{ name: 'fig1_approved.png', caption: 'Approved result' }],
  });
  expect(await screen.findByRole('link', { name: /Open static brief/ })).toBeTruthy();
  expect(screen.getByRole('img', { name: 'Approved result' })).toBeTruthy();
  expect(screen.getByRole('link', { name: /Open PDF used/ })).toBeTruthy();
});
