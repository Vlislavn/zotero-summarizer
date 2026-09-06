// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import Pending from './Pending.jsx';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it.each(['apply', 'reject'])('does not %s changes hidden by the title filter', async (action) => {
  const writes = [];
  vi.stubGlobal('fetch', vi.fn(async (path, options = {}) => {
    let data;
    if (path === '/api/zotero/collections') data = { items: [] };
    else if (path.startsWith('/api/pending?')) data = { items: [1, 2, 3, 4].map((id) => ({
      id, item_key: `P${id}`, item_title: id === 1 ? 'Alpha' : `Beta ${id}`,
      status: 'pending', change_type: 'add_note', payload_json: { note: 'Note' },
    })) };
    else if (path === `/api/pending/${action}`) {
      writes.push(JSON.parse(options.body));
      data = { applied: 1, rejected: 1, failed: 0 };
    } else throw new Error(`Unexpected request: ${path}`);
    return new Response(JSON.stringify(data));
  }));
  render(<Pending />);
  const filter = await screen.findByPlaceholderText('Filter by title…');
  fireEvent.click(screen.getByRole('button', { name: 'Select all' }));
  fireEvent.change(filter, { target: { value: 'absent' } });
  const button = screen.getByRole('button', { name: action === 'apply' ? 'Apply selected' : 'Reject selected' });
  expect(button.disabled).toBe(true);
  fireEvent.keyDown(document.body, { key: action === 'apply' ? 'a' : 'r' });
  expect(writes).toEqual([]);
  fireEvent.change(filter, { target: { value: 'Alpha' } });
  expect(screen.getAllByRole('checkbox')).toHaveLength(1);
  fireEvent.click(button);
  await waitFor(() => expect(writes).toHaveLength(1));
  expect(writes[0].change_ids).toEqual([1]);
});

it('does not send pending writes while typing in the collection selector', async () => {
  const writes = [];
  vi.stubGlobal('fetch', vi.fn(async (path, options = {}) => {
    let data;
    if (path === '/api/zotero/collections') data = { items: [{ key: 'C1', name: 'Algorithms' }] };
    else if (path.startsWith('/api/pending?')) data = { items: [{
      id: 1, item_key: 'P1', item_title: 'Paper', status: 'pending',
      change_type: 'add_to_collection', payload_json: { collection_key: 'C1' },
    }] };
    else if (path === '/api/pending/apply') {
      writes.push(JSON.parse(options.body));
      data = { applied: 1, failed: 0 };
    } else throw new Error(`Unexpected request: ${path}`);
    return { ok: true, text: async () => JSON.stringify(data) };
  }));
  render(<Pending />);
  const selector = await screen.findByRole('combobox');
  fireEvent.click(screen.getByRole('checkbox'));
  selector.focus();

  fireEvent.keyDown(selector, { key: 'a' });

  expect(document.activeElement).toBe(selector);
  expect(writes).toEqual([]);
  fireEvent.click(screen.getByRole('button', { name: 'Apply selected' }));
  await waitFor(() => expect(writes).toEqual([{ change_ids: [1], force: false, retry: false }]));
  expect(await screen.findByText('Selected changes applied.')).toBeTruthy();
});
