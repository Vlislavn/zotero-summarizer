// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import Pending from './Pending.jsx';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function mount(receipt) {
  const writes = [];
  let rows = [1, 2].map((id) => ({ id, item_key: `P${id}`, item_title: `Paper ${id}`,
    status: 'pending', change_type: 'add_note', payload_json: { note: 'Saved note' } }));
  vi.stubGlobal('fetch', vi.fn(async (path, options = {}) => {
    let data;
    if (path === '/api/zotero/collections') data = { items: [] };
    else if (path.startsWith('/api/pending?')) data = { items: rows };
    else if (path === '/api/pending/reject') {
      writes.push(JSON.parse(options.body));
      if (receipt.updated === 2) rows = [];
      data = receipt;
    } else throw new Error(`Unexpected request: ${path}`);
    return new Response(JSON.stringify(data));
  }));
  render(<Pending />);
  return writes;
}

it.each([{ updated: 0 }, { updated: 1 }, {}, { updated: '2' }])(
  'does not announce success for an unconfirmed selected rejection %j', async (receipt) => {
    const writes = mount(receipt);
    await screen.findByText('Paper 1');
    fireEvent.click(screen.getByRole('button', { name: 'Select all' }));
    fireEvent.click(screen.getByRole('button', { name: 'Reject selected' }));
    await waitFor(() => expect(writes).toHaveLength(1));
    await waitFor(() => expect(screen.getAllByRole('checkbox').every((node) => node.checked)).toBe(true));
    expect(screen.queryByText('Selected changes rejected.')).toBeNull();
    expect(writes[0].change_ids).toEqual([1, 2]);
  },
);

it('announces a complete confirmed rejection and reloads the actual queue', async () => {
  const writes = mount({ updated: 2 });
  await screen.findByText('Paper 1');
  fireEvent.click(screen.getByRole('button', { name: 'Select all' }));
  fireEvent.click(screen.getByRole('button', { name: 'Reject selected' }));
  await screen.findByText('Selected changes rejected.');
  await waitFor(() => expect(screen.queryByText('Paper 1')).toBeNull());
  expect(writes[0].change_ids).toEqual([1, 2]);
});
