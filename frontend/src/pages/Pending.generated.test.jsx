// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import Pending from './Pending.jsx';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const preview = {
  source: 'deep_review', change_type: 'upsert_note', item_key: 'LIBR0151',
  target: { database_path: '/owned/zotero.sqlite', device: 1, inode: 2, library_id: 1, library_type: 'user' },
  review_revision: 'f41d010f-7519-4108-8dab-c094537fbbd0',
  source_sha256: 'a'.repeat(64), body_sha256: 'b'.repeat(64),
  payload: { note_title: 'Deep digest', marker: 'zs:note_type=digest',
    note_html: '<p>Literal & full payload</p><img src=x onerror="approve()">' },
  preview_sha256: 'c'.repeat(64),
};

function approval(snapshot = preview) {
  return snapshot.target ? {
    version: 1, change_id: 151, item_key: snapshot.item_key, change_type: snapshot.change_type,
    payload: snapshot.payload, edit_revision: 0, target: snapshot.target,
    generated_preview_sha256: snapshot.preview_sha256, preview_sha256: 'd'.repeat(64),
  } : null;
}

function wire(status = 'pending', snapshot = preview, additionalRows = []) {
  const writes = [];
  vi.stubGlobal('fetch', vi.fn(async (path, options = {}) => {
    let data;
    if (path === '/api/zotero/collections') data = { items: [] };
    else if (path.startsWith('/api/pending?')) data = { items: [{
      id: 151, item_key: preview.item_key, item_title: 'Owned generated note', status,
      change_type: 'upsert_note', payload_json: snapshot.payload, generated_preview: snapshot,
      approval_preview: approval(snapshot),
      audit: [{ event: 'staged', actor: 'system', source: 'deep_review' }],
    }, ...additionalRows] };
    else if (path === '/api/pending/apply') {
      writes.push(JSON.parse(options.body));
      data = { applied: 1 + additionalRows.length, failed: 0 };
    } else throw new Error(`Unexpected request: ${path}`);
    return new Response(JSON.stringify(data));
  }));
  return writes;
}

it('shows the whole escaped immutable payload and submits the displayed common identity, not the inner hash', async () => {
  const writes = wire();
  render(<Pending />);
  const literal = await screen.findByLabelText('Generated mutation preview');
  expect(literal.textContent).toBe(JSON.stringify(preview, null, 2));
  expect(screen.getByLabelText('Approval mutation preview').textContent).toBe(JSON.stringify(approval(), null, 2));
  expect(document.querySelector('img')).toBeNull();
  expect(screen.queryByRole('button', { name: 'Save edit' })).toBeNull();
  expect(writes).toEqual([]);
  fireEvent.click(screen.getByRole('checkbox'));
  fireEvent.click(screen.getByRole('button', { name: 'Apply selected' }));
  await waitFor(() => expect(writes).toEqual([{
    change_ids: [151], force: false, retry: false, previews: { 151: approval().preview_sha256 },
  }]));
});

it('keeps legacy unbound generated notes rejectable but never applicable', async () => {
  const writes = wire('pending', { ...preview, target: null });
  render(<Pending />);
  await screen.findByLabelText('Generated mutation preview');
  expect(screen.getByText('This generated note has no bound Zotero target. Reject it or regenerate the review before applying.')).toBeTruthy();
  fireEvent.click(screen.getByRole('checkbox'));
  expect(screen.getByRole('button', { name: 'Apply selected' }).disabled).toBe(true);
  expect(screen.getByRole('button', { name: 'Reject selected' }).disabled).toBe(false);
  fireEvent.keyDown(window, { key: 'a' });
  expect(writes).toEqual([]);
});

it('does not offer retry for a failed generated note without a bound target', async () => {
  const writes = wire('failed', { ...preview, target: null });
  render(<Pending />);
  await screen.findByLabelText('Generated mutation preview');
  fireEvent.click(screen.getByRole('button', { name: 'Failed' }));
  expect(screen.queryByRole('button', { name: 'Retry' })).toBeNull();
  expect(writes).toEqual([]);
});

it('retries a bound failed generated note with its common outer hash', async () => {
  const writes = wire('failed');
  render(<Pending />);
  await screen.findByLabelText('Generated mutation preview');
  fireEvent.click(screen.getByRole('button', { name: 'Failed' }));
  const retry = await screen.findByRole('button', { name: 'Retry' });
  await waitFor(() => expect(retry.disabled).toBe(false));
  fireEvent.click(retry);
  await waitFor(() => expect(writes).toEqual([{
    change_ids: [151], previews: { 151: approval().preview_sha256 }, force: false, retry: true,
  }]));
});

it('retains uncertain delivery as a non-retryable reconciliation state', async () => {
  const writes = wire('applying');
  render(<Pending />);
  await screen.findByLabelText('Generated mutation preview');
  fireEvent.click(screen.getByRole('button', { name: 'Needs reconciliation' }));
  expect((await screen.findByRole('alert')).textContent).toBe(
    'Delivery outcome is uncertain. Reconcile Zotero against the backup before any new action; retry is blocked.',
  );
  expect(screen.queryByRole('button', { name: 'Retry' })).toBeNull();
  expect(screen.queryByRole('button', { name: 'Apply selected' })).toBeNull();
  expect(screen.getByRole('checkbox').disabled).toBe(true);
  expect(writes).toEqual([]);
});

it.each([true, false])('uses one complete mixed common map, refusing a missing ordinary snapshot: %s', async (available) => {
  const payload = { collection_key: 'COLL1', collection_path: 'Inbox' };
  const ordinaryApproval = {
    ...approval(), change_id: 7, item_key: 'ORDINARY', change_type: 'remove_from_collection', payload,
    edit_revision: 3, generated_preview_sha256: null, preview_sha256: 'e'.repeat(64),
  };
  const ordinary = {
    id: 7, item_key: 'ORDINARY', item_title: 'Explicit collection removal', status: 'pending',
    change_type: 'remove_from_collection', payload_json: payload,
    approval_preview: available ? ordinaryApproval : null, audit: [], backup_path: null,
  };
  const writes = wire('pending', preview, [ordinary]);
  render(<Pending />);
  await screen.findByLabelText('Generated mutation preview');
  expect(screen.getAllByLabelText('Approval mutation preview').map((node) => node.textContent)).toEqual([
    JSON.stringify(approval(), null, 2),
    JSON.stringify(available ? ordinaryApproval : { payload, target: null }, null, 2),
  ]);
  fireEvent.click(screen.getByRole('button', { name: 'Select all' }));
  const apply = screen.getByRole('button', { name: 'Apply selected' });
  expect(apply.disabled).toBe(!available);
  fireEvent.click(apply);
  if (available) {
    await waitFor(() => expect(writes).toEqual([{
      change_ids: [151, 7], previews: { 151: approval().preview_sha256, 7: ordinaryApproval.preview_sha256 }, force: false, retry: false,
    }]));
    expect(await screen.findByText('2 change(s) applied; 0 failed.')).toBeTruthy();
  } else {
    fireEvent.keyDown(window, { key: 'a' });
    expect(writes).toEqual([]);
    expect(screen.getByRole('button', { name: 'Reject selected' }).disabled).toBe(false);
  }
});
