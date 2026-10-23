// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import * as golden from '../../api/goldenApi.js';
import * as library from '../../api/libraryApi.js';
import * as settings from '../../api/settingsApi.js';
import ReadNextView from './ReadNextView.jsx';

vi.mock('../../api/goldenApi.js');
vi.mock('../../api/libraryApi.js');
vi.mock('../../api/settingsApi.js');
vi.mock('../../hooks/useServerAvailable.js', () => ({ default: () => true }));
vi.mock('../paper/TagOfInterestEditor.jsx', () => ({ default: ({ onChanged }) => <button onClick={() => onChanged()}>Native tag ACK</button> }));
const clients = [];
afterEach(() => { cleanup(); clients.splice(0).forEach(client => client.clear()); vi.resetAllMocks(); vi.restoreAllMocks(); });

it.each([
  ['submit', true], ['delete', true], ['submit', false], ['delete', false], ['refresh', true], ['refresh', false],
])('keeps same-paper live note ownership for %s completion with dirty=%s', async (operation, dirty) => {
  let detail = { item_key: 'P1', title: 'Race paper', has_pdf: false, tags: [], collections: [],
    user_note: 'Server note', deep_review: { digest: { tldr: 'Retained context' } },
    verdict: operation === 'delete' ? { user_priority: 'should_read', comment: 'Initial' } : null };
  golden.fetchReviewDetail.mockImplementation(async () => detail);
  library.fetchDeepReviewStatus.mockResolvedValue({ status: 'idle' });
  settings.fetchLlmReachability.mockResolvedValue({ stages: [] });
  let release;
  const response = new Promise(resolve => { release = resolve; });
  (operation === 'submit' ? golden.submitVerdict : golden.deleteVerdict).mockReturnValue(response);
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  clients.push(client);
  const onSaved = vi.fn();
  render(<QueryClientProvider client={client}><MemoryRouter><ReadNextView
    items={[{ item_key: 'P1', title: 'Race paper' }]} totalUnread={1} status="ready" modelReady={false}
    onSaved={onSaved}
  /></MemoryRouter></QueryClientProvider>);
  fireEvent.click(screen.getByText('Race paper', { exact: true }));
  const note = await screen.findByRole('textbox', { name: 'Manual notes', exact: true });
  try {
    if (operation === 'refresh') {
      if (dirty) fireEvent.change(note, { target: { value: 'New dirty note while verdict is in flight' } });
      fireEvent.click(screen.getByRole('button', { name: 'Native tag ACK', exact: true }));
    } else if (operation === 'submit') {
      fireEvent.click(screen.getByRole('button', { name: 'Should read', exact: true }));
      fireEvent.click(screen.getByRole('button', { name: 'Save verdict', exact: true }));
      await waitFor(() => expect(golden.submitVerdict).toHaveBeenCalledOnce());
    } else {
      fireEvent.click(screen.getByRole('button', { name: 'Delete', exact: true }));
      await waitFor(() => expect(golden.deleteVerdict).toHaveBeenCalledOnce());
    }
    if (dirty && operation !== 'refresh') fireEvent.change(note, { target: { value: 'New dirty note while verdict is in flight' } });
    detail = { ...detail, verdict: operation === 'submit' ? { user_priority: 'should_read', comment: '' } : null };
  } finally {
    await act(async () => { release(operation === 'submit' ? { id: 1 } : { deleted: true }); });
  }
  if (dirty) {
    expect(screen.getByRole('textbox', { name: 'Manual notes', exact: true }).value)
      .toBe('New dirty note while verdict is in flight');
    expect(screen.getByTestId('expanded-paper-panel')).toBeTruthy();
    expect(onSaved).not.toHaveBeenCalled();
  } else {
    await waitFor(() => expect(onSaved).toHaveBeenCalledOnce());
    if (operation === 'refresh') expect(screen.getByTestId('expanded-paper-panel')).toBeTruthy();
    else expect(screen.queryByTestId('expanded-paper-panel')).toBeNull();
  }
});
