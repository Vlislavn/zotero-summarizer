// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import Pending from './Pending.jsx';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

const target = { database_path: '/owned/zotero.sqlite', device: 1, inode: 2, library_id: 1, library_type: 'user' };

function presented(id = 1, payload = { note_title: 'Saved title', note_html: '<p>Saved body</p>' }, type = 'add_note', status = 'pending') {
  return {
    id, item_key: `P${id}`, item_title: 'Paper', status, change_type: type, payload_json: payload,
    approval_preview: {
      version: 1, change_id: id, item_key: `P${id}`, change_type: type, payload,
      edit_revision: 0, target, generated_preview_sha256: null, preview_sha256: 'a'.repeat(64),
    },
    audit: [], backup_path: null,
  };
}

function wire({ rows, apply, edit, reject }) {
  const writes = [];
  const reads = [];
  vi.stubGlobal('fetch', vi.fn(async (path, options = {}) => {
    if (path === '/api/zotero/collections') return new Response(JSON.stringify({ items: [] }));
    if (path.startsWith('/api/pending?')) {
      reads.push(path);
      return new Response(JSON.stringify({ items: rows(path) }));
    }
    const body = JSON.parse(options.body);
    writes.push({ path, body });
    if (path === '/api/pending/apply') return apply(body);
    if (path === '/api/pending/change/1') return edit(body);
    if (path === '/api/pending/reject') return reject ? reject(body) : new Response(JSON.stringify({ updated: 1 }));
    throw new Error(`Unexpected request: ${path}`);
  }));
  return { writes, reads };
}

it.each([
  ['add_note', { note_title: 'Saved title', note_html: '<p>Saved body</p>' }, ['Note title', 'Note HTML']],
  ['tag_changes', { add_tags: ['keep'], remove_tags: ['old'] }, ['Add tags (comma-separated)', 'Remove tags (comma-separated)']],
  ['add_to_collection', { collection_key: '' }, ['Target collection']],
  ['remove_from_collection', { collection_key: '' }, ['Target collection']],
])('natively labels each field of repeated %s editors without changing selection or writing', async (type, payload, names) => {
  const { writes } = wire({ rows: () => [presented(1, payload, type), presented(2, payload, type)] });
  render(<Pending />);

  for (const name of names) {
    const controls = await screen.findAllByLabelText(name, { exact: true });
    expect(controls).toHaveLength(2);
    expect(new Set(controls.map((control) => control.id)).size).toBe(2);
    for (const control of controls) {
      expect(control.id).not.toBe('');
      expect(control.labels).toHaveLength(1);
      expect(control.labels[0].textContent).toBe(name);
      expect(control.labels[0].control).toBe(control);
    }
  }
  const checkboxes = screen.getAllByRole('checkbox');
  expect(checkboxes).toHaveLength(2);
  fireEvent.click(checkboxes[0]);
  expect(checkboxes.map((checkbox) => checkbox.checked)).toEqual([true, false]);
  expect(writes).toEqual([]);
});

it.each(['apply', 'reject'])('does not %s changes hidden by the title filter', async (action) => {
  const writes = [];
  vi.stubGlobal('fetch', vi.fn(async (path, options = {}) => {
    let data;
    if (path === '/api/zotero/collections') data = { items: [] };
    else if (path.startsWith('/api/pending?')) data = { items: [1, 2, 3, 4].map((id) => ({
      ...presented(id), item_title: id === 1 ? 'Alpha' : `Beta ${id}`,
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
  expect(writes[0].previews).toEqual(action === 'apply' ? { 1: 'a'.repeat(64) } : undefined);
});

it('does not send pending writes while typing in the collection selector', async () => {
  const writes = [];
  vi.stubGlobal('fetch', vi.fn(async (path, options = {}) => {
    let data;
    if (path === '/api/zotero/collections') data = { items: [{ key: 'C1', name: 'Algorithms' }] };
    else if (path.startsWith('/api/pending?')) data = { items: [{
      ...presented(1, { collection_key: 'C1' }, 'add_to_collection'),
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
  await waitFor(() => expect(writes).toEqual([{
    change_ids: [1], previews: { 1: 'a'.repeat(64) }, force: false, retry: false,
  }]));
  expect(await screen.findByText('1 change(s) applied; 0 failed.')).toBeTruthy();
});

it.each(['edit', 'identical save'])('retires selection after %s, then requires the newly rendered revision', async (kind) => {
  let row = presented();
  const { writes } = wire({
    rows: () => [row],
    edit: ({ payload }) => {
      row = { ...row, payload_json: payload, approval_preview: {
        ...row.approval_preview, payload, edit_revision: 1, preview_sha256: 'b'.repeat(64),
      } };
      return new Response(JSON.stringify({ payload }));
    },
    apply: () => new Response(JSON.stringify({ applied: 1, failed: 0 })),
  });
  render(<Pending />);
  await screen.findByLabelText('Approval mutation preview');
  fireEvent.click(screen.getByRole('checkbox'));
  if (kind === 'edit') fireEvent.change(screen.getAllByRole('textbox')[0], { target: { value: 'Changed title' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save edit' }));

  await waitFor(() => expect(screen.getByLabelText('Approval mutation preview').textContent).toBe(JSON.stringify(row.approval_preview, null, 2)));
  expect(screen.getByRole('checkbox').checked).toBe(false);
  expect(screen.getByRole('button', { name: 'Apply selected' }).disabled).toBe(true);
  fireEvent.keyDown(window, { key: 'a' });
  expect(writes.map((write) => write.path)).toEqual(['/api/pending/change/1']);
  fireEvent.click(screen.getByRole('checkbox'));
  fireEvent.click(screen.getByRole('button', { name: 'Apply selected' }));
  await waitFor(() => expect(writes[1]?.body).toEqual({
    change_ids: [1], previews: { 1: 'b'.repeat(64) }, force: false, retry: false,
  }));
});

it.each(['payload', 'target'])('submits A without a hidden GET, then retires stale %s selection instead of applying refreshed B', async (changed) => {
  const a = presented();
  let row = a;
  const { reads, writes } = wire({
    rows: () => [row],
    apply: () => new Response(JSON.stringify({ error: 'pending_write_conflict', message: 'Stale snapshot' }), { status: 409 }),
  });
  render(<Pending />);
  await screen.findByLabelText('Approval mutation preview');
  fireEvent.click(screen.getByRole('checkbox'));
  const bPayload = changed === 'payload' ? { note_title: 'Durable B', note_html: '<p>Unseen B body</p>' } : a.payload_json;
  row = { ...a, payload_json: bPayload, approval_preview: {
    ...a.approval_preview, payload: bPayload, edit_revision: changed === 'payload' ? 1 : 0,
    target: changed === 'target' ? { ...target, database_path: '/owned/other.sqlite', inode: 3 } : target,
    preview_sha256: 'b'.repeat(64),
  } };
  expect(reads).toHaveLength(1);
  fireEvent.click(screen.getByRole('button', { name: 'Apply selected' }));

  expect(await screen.findByText('The selected changes are no longer current. Review the refreshed payload and target, then reselect.')).toBeTruthy();
  await waitFor(() => expect(reads).toHaveLength(2));
  expect(writes).toEqual([{ path: '/api/pending/apply', body: {
    change_ids: [1], previews: { 1: a.approval_preview.preview_sha256 }, force: false, retry: false,
  } }]);
  await waitFor(() => expect(screen.getByLabelText('Approval mutation preview').textContent).toBe(JSON.stringify(row.approval_preview, null, 2)));
  expect(screen.getByRole('checkbox').checked).toBe(false);
  fireEvent.keyDown(window, { key: 'a' });
  expect(writes).toHaveLength(1);
});

it('keeps a received unreadable HTTP 409 as an unconfirmed delivery', async () => {
  const row = presented();
  const { reads, writes } = wire({
    rows: () => [row],
    apply: () => ({
      ok: false,
      status: 409,
      statusText: 'Conflict',
      text: async () => { throw new Error('Response body could not be read'); },
    }),
  });
  render(<Pending />);
  await screen.findByLabelText('Approval mutation preview');
  fireEvent.click(screen.getByRole('checkbox'));
  fireEvent.click(screen.getByRole('button', { name: 'Apply selected' }));

  expect(await screen.findByText(
    'Response body could not be read Delivery is unconfirmed. Inspect the refreshed state before any new action.',
  )).toBeTruthy();
  expect(screen.queryByText('The selected changes are no longer current. Review the refreshed payload and target, then reselect.')).toBeNull();
  await waitFor(() => expect(reads).toHaveLength(2));
  expect(writes).toEqual([{ path: '/api/pending/apply', body: {
    change_ids: [1], previews: { 1: row.approval_preview.preview_sha256 }, force: false, retry: false,
  } }]);
});

it.each([
  ['decoded', () => new Response(JSON.stringify({ detail: 'The preview is stale.' }), { status: 409 })],
  ['empty-body', () => ({ ok: false, status: 409, statusText: 'Conflict', text: async () => '' })],
])('treats a fully read %s HTTP 409 as a stale approval preview', async (_label, response) => {
  const row = presented();
  const { reads, writes } = wire({ rows: () => [row], apply: () => response() });
  render(<Pending />);
  await screen.findByLabelText('Approval mutation preview');
  fireEvent.click(screen.getByRole('checkbox'));
  fireEvent.click(screen.getByRole('button', { name: 'Apply selected' }));

  expect(await screen.findByText(
    'The selected changes are no longer current. Review the refreshed payload and target, then reselect.',
  )).toBeTruthy();
  expect(screen.queryByText(/Delivery is unconfirmed/)).toBeNull();
  await waitFor(() => expect(reads).toHaveLength(2));
  expect(writes).toEqual([{ path: '/api/pending/apply', body: {
    change_ids: [1], previews: { 1: row.approval_preview.preview_sha256 }, force: false, retry: false,
  } }]);
});

it.each(['pending', 'failed'])('preserves the captured %s map through force confirmation', async (status) => {
  const a = presented(1, undefined, 'add_note', status);
  let row = a;
  const { writes, reads } = wire({
    rows: () => [row],
    apply: ({ force }) => new Response(JSON.stringify(force ? { applied: 1, failed: 0 } : { error: 'zotero_running' })),
  });
  vi.spyOn(window, 'confirm').mockImplementation(() => {
    expect(screen.getByText(`status: ${status}`)).toBeTruthy();
    row = { ...a, approval_preview: { ...a.approval_preview, preview_sha256: 'b'.repeat(64) } };
    return true;
  });
  render(<Pending />);
  await screen.findByLabelText('Approval mutation preview');
  if (status === 'failed') {
    fireEvent.click(screen.getByRole('button', { name: 'Failed' }));
    await waitFor(() => expect(reads).toHaveLength(2));
    fireEvent.click(await screen.findByRole('button', { name: 'Retry' }));
  } else {
    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', { name: 'Apply selected' }));
  }
  await waitFor(() => expect(writes).toHaveLength(2));
  expect(writes.map((write) => write.body)).toEqual([false, true].map((force) => ({
    change_ids: [1], previews: { 1: a.approval_preview.preview_sha256 }, force, retry: status === 'failed',
  })));
  expect(window.confirm).toHaveBeenCalledTimes(1);
  expect(await screen.findByText('1 change(s) applied; 0 failed.')).toBeTruthy();
});

it('does not reselect or dispatch when force is cancelled', async () => {
  const row = presented();
  const { writes } = wire({ rows: () => [row], apply: () => new Response(JSON.stringify({ error: 'zotero_running' })) });
  vi.spyOn(window, 'confirm').mockReturnValue(false);
  render(<Pending />);
  await screen.findByLabelText('Approval mutation preview');
  fireEvent.click(screen.getByRole('checkbox'));
  fireEvent.click(screen.getByRole('button', { name: 'Apply selected' }));
  expect(await screen.findByText('Apply cancelled — Zotero is running.')).toBeTruthy();
  await waitFor(() => expect(screen.getByRole('checkbox').checked).toBe(false));
  expect(writes).toHaveLength(1);
});

it('keeps pending status until the response and never retries an uncertain delivery', async () => {
  let row = presented();
  let fail;
  const waiting = new Promise((_resolve, reject) => { fail = reject; });
  const { writes, reads } = wire({ rows: () => [row], apply: () => waiting });
  render(<Pending />);
  await screen.findByLabelText('Approval mutation preview');
  fireEvent.click(screen.getByRole('checkbox'));
  fireEvent.click(screen.getByRole('button', { name: 'Apply selected' }));
  expect(screen.getByText('status: pending')).toBeTruthy();
  expect(screen.queryByText('status: applied')).toBeNull();
  expect(screen.getByRole('button', { name: 'Apply selected' }).disabled).toBe(true);
  fireEvent.keyDown(window, { key: 'a' });
  expect(writes).toHaveLength(1);
  row = { ...row, status: 'applying', backup_path: '/owned/backup.sqlite', audit: [{ event: 'approved', actor: 'human', source: 'ui' }] };
  fail(new Error('Connection lost'));

  expect(await screen.findByText('Connection lost Delivery is unconfirmed. Inspect the refreshed state before any new action.')).toBeTruthy();
  await waitFor(() => expect(reads).toHaveLength(2));
  expect(await screen.findByText('Delivery outcome is uncertain. Reconcile Zotero against the backup before any new action; retry is blocked.')).toBeTruthy();
  expect(screen.getAllByRole('alert')).toHaveLength(2);
  expect(screen.getByText('Backup: /owned/backup.sqlite')).toBeTruthy();
  expect(document.querySelector('details pre').textContent).toBe(JSON.stringify(row.audit, null, 2));
  expect(screen.queryByRole('button', { name: 'Retry' })).toBeNull();
  expect(screen.getByRole('checkbox').disabled).toBe(true);
  fireEvent.keyDown(window, { key: 'a' });
  expect(writes).toHaveLength(1);
});

it('shows the full escaped ordinary failed payload and target before explicit retry', async () => {
  const payload = { note_title: 'Full note', note_html: `<p>${'Complete body & '.repeat(30)}</p><img src=x onerror="approve()">` };
  const row = presented(1, payload, 'add_note', 'failed');
  const { writes, reads } = wire({ rows: () => [row], apply: () => new Response(JSON.stringify({ applied: 0, failed: 1 })) });
  render(<Pending />);
  await screen.findByLabelText('Approval mutation preview');
  fireEvent.click(screen.getByRole('button', { name: 'Failed' }));
  await waitFor(() => expect(reads).toHaveLength(2));
  expect(screen.getByLabelText('Approval mutation preview').textContent).toBe(JSON.stringify(row.approval_preview, null, 2));
  expect(document.querySelector('img')).toBeNull();
  fireEvent.click(await screen.findByRole('button', { name: 'Retry' }));
  await waitFor(() => expect(writes[0]?.body).toEqual({
    change_ids: [1], previews: { 1: row.approval_preview.preview_sha256 }, force: false, retry: true,
  }));
  expect(await screen.findByText('0 change(s) applied; 1 failed. Open the Failed tab to retry.')).toBeTruthy();
});

it.each([null, { preview_sha256: 'a'.repeat(64) }])('blocks Apply but not Reject without a common snapshot: %j', async (approval) => {
  const row = { ...presented(), approval_preview: approval };
  const { writes } = wire({ rows: () => [row] });
  render(<Pending />);
  await screen.findByLabelText('Approval mutation preview');
  fireEvent.click(screen.getByRole('checkbox'));
  expect(screen.getByRole('button', { name: 'Apply selected' }).disabled).toBe(true);
  fireEvent.keyDown(window, { key: 'a' });
  expect(writes).toEqual([]);
  fireEvent.click(screen.getByRole('button', { name: 'Reject selected' }));
  await waitFor(() => expect(writes[0]).toEqual({ path: '/api/pending/reject', body: { change_ids: [1] } }));
});

it('does not offer Failed retry without a common approval snapshot', async () => {
  const row = { ...presented(1, undefined, 'add_note', 'failed'), approval_preview: null };
  const { writes, reads } = wire({ rows: () => [row] });
  render(<Pending />);
  await screen.findByLabelText('Approval mutation preview');
  fireEvent.click(screen.getByRole('button', { name: 'Failed' }));
  await waitFor(() => expect(reads).toHaveLength(2));
  await screen.findByLabelText('Approval mutation preview');
  expect(screen.queryByRole('button', { name: 'Retry' })).toBeNull();
  expect(writes).toEqual([]);
});

it('does not acknowledge Applied from a malformed success response', async () => {
  const row = presented();
  const { writes, reads } = wire({ rows: () => [row], apply: () => new Response('{}') });
  render(<Pending />);
  await screen.findByLabelText('Approval mutation preview');
  fireEvent.click(screen.getByRole('checkbox'));
  fireEvent.click(screen.getByRole('button', { name: 'Apply selected' }));
  expect(await screen.findByText('The server did not confirm the Apply outcome. Delivery is unconfirmed. Inspect the refreshed state before any new action.')).toBeTruthy();
  await waitFor(() => expect(reads).toHaveLength(2));
  expect(writes).toHaveLength(1);
  expect(screen.queryByText('status: applied')).toBeNull();
});

it.each(['pending', 'applied'])('keeps refreshed %s snapshot B retired after a deferred Reject failure for A', async (status) => {
  const a = presented();
  const payload = { note_title: 'Replacement B', note_html: '<p>Replacement body B</p>' };
  const b = { ...a, status, payload_json: payload, approval_preview: {
    ...a.approval_preview, payload, edit_revision: 1, preview_sha256: 'b'.repeat(64),
  } };
  let row = a;
  let failReject;
  const rejectedRequest = new Promise((_resolve, reject) => { failReject = reject; });
  const { writes, reads } = wire({
    rows: (path) => new URLSearchParams(path.split('?')[1]).get('status') === row.status ? [row] : [],
    reject: () => rejectedRequest,
    apply: () => new Response(JSON.stringify({ applied: 1, failed: 0 })),
  });
  render(<Pending />);
  expect((await screen.findByLabelText('Approval mutation preview')).textContent).toBe(JSON.stringify(a.approval_preview, null, 2));
  fireEvent.click(screen.getByRole('checkbox'));
  expect(screen.getByRole('checkbox').checked).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: 'Reject selected' }));
  await waitFor(() => expect(writes).toEqual([{ path: '/api/pending/reject', body: { change_ids: [a.id] } }]));
  expect(screen.getByText('status: rejected')).toBeTruthy();
  expect(screen.getByRole('checkbox').checked).toBe(false);

  row = b;
  fireEvent.click(screen.getByRole('button', { name: 'Applied' }));
  if (status === 'pending') {
    await screen.findByText('No applied changes.');
    fireEvent.click(screen.getByRole('button', { name: 'Pending' }));
  }
  await waitFor(() => expect(screen.getByLabelText('Approval mutation preview').textContent).toBe(JSON.stringify(b.approval_preview, null, 2)));
  const expectedStatuses = status === 'pending' ? ['pending', 'applied', 'pending'] : ['pending', 'applied'];
  expect(reads.map((path) => new URLSearchParams(path.split('?')[1]).get('status'))).toEqual(expectedStatuses);
  expect(screen.getByText(`status: ${status}`)).toBeTruthy();
  expect(screen.getByRole('checkbox').checked).toBe(false);
  expect(screen.getByRole('checkbox').disabled).toBe(status !== 'pending');
  if (status === 'pending') expect(screen.getByRole('button', { name: 'Apply selected' }).disabled).toBe(true);

  const failure = new Error('Deferred Reject failed');
  await act(async () => {
    failReject(failure);
    await expect(rejectedRequest).rejects.toBe(failure);
  });

  expect(screen.getByLabelText('Approval mutation preview').textContent).toBe(JSON.stringify(b.approval_preview, null, 2));
  expect(screen.getByText(`status: ${status}`)).toBeTruthy();
  expect(screen.getByRole('checkbox').checked).toBe(false);
  expect(screen.getByRole('checkbox').disabled).toBe(status !== 'pending');
  expect(reads).toHaveLength(expectedStatuses.length);
  expect(writes).toEqual([{ path: '/api/pending/reject', body: { change_ids: [a.id] } }]);
  if (status === 'pending') {
    const apply = screen.getByRole('button', { name: 'Apply selected' });
    expect(apply.disabled).toBe(true);
    fireEvent.click(apply);
    fireEvent.keyDown(window, { key: 'a' });
    expect(writes).toHaveLength(1);
    fireEvent.click(screen.getByRole('checkbox'));
    expect(screen.getByRole('checkbox').checked).toBe(true);
    expect(apply.disabled).toBe(false);
    fireEvent.click(apply);
    await waitFor(() => expect(writes).toEqual([
      { path: '/api/pending/reject', body: { change_ids: [a.id] } },
      { path: '/api/pending/apply', body: { change_ids: [b.id], previews: { [b.id]: b.approval_preview.preview_sha256 }, force: false, retry: false } },
    ]));
  } else {
    expect(screen.queryByRole('button', { name: 'Apply selected' })).toBeNull();
  }
});

it('preserves same-generation Reject rollback without restoring a different snapshot', async () => {
  const a = presented();
  let failReject;
  const rejectedRequest = new Promise((_resolve, reject) => { failReject = reject; });
  const { writes, reads } = wire({ rows: () => [a], reject: () => rejectedRequest });
  render(<Pending />);
  await screen.findByLabelText('Approval mutation preview');
  fireEvent.click(screen.getByRole('checkbox'));
  fireEvent.click(screen.getByRole('button', { name: 'Reject selected' }));
  await waitFor(() => expect(writes).toEqual([{ path: '/api/pending/reject', body: { change_ids: [a.id] } }]));
  expect(screen.getByText('status: rejected')).toBeTruthy();
  expect(screen.getByRole('checkbox').checked).toBe(false);
  expect(screen.getByRole('button', { name: 'Apply selected' }).disabled).toBe(true);

  const failure = new Error('Same-generation Reject failed');
  await act(async () => {
    failReject(failure);
    await expect(rejectedRequest).rejects.toBe(failure);
  });

  expect(screen.getByLabelText('Approval mutation preview').textContent).toBe(JSON.stringify(a.approval_preview, null, 2));
  expect(screen.getByText('status: pending')).toBeTruthy();
  expect(screen.getByRole('checkbox').checked).toBe(true);
  expect(screen.getByRole('button', { name: 'Apply selected' }).disabled).toBe(false);
  expect(screen.getByRole('alert').textContent).toBe(failure.message);
  expect(reads).toHaveLength(1);
  expect(writes).toEqual([{ path: '/api/pending/reject', body: { change_ids: [a.id] } }]);
});
