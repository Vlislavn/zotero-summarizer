// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { fetchTags, updateItemTags } from '../../api/libraryApi.js';
import TagOfInterestEditor from './TagOfInterestEditor.jsx';

vi.mock('../../api/libraryApi.js', () => ({ fetchTags: vi.fn(), updateItemTags: vi.fn() }));
vi.mock('../../hooks/useServerAvailable.js', () => ({ default: () => true }));
const clients = [];
beforeEach(() => {
  vi.resetAllMocks();
  fetchTags.mockResolvedValue({ items: [] });
});
afterEach(() => {
  cleanup();
  clients.splice(0).forEach(client => client.clear());
  vi.restoreAllMocks();
});

function show(onChanged = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  clients.push(client);
  const editor = (itemKey) => <QueryClientProvider client={client}>
    <TagOfInterestEditor itemKey={itemKey} tags={['Saved tag']} onChanged={onChanged} />
  </QueryClientProvider>;
  const view = render(editor('P1'));
  return { onChanged, changePaper: (key) => view.rerender(editor(key)) };
}
const input = () => screen.getByPlaceholderText('tag… (pick or type)');
function submit(text) {
  fireEvent.change(input(), { target: { value: text } });
  fireEvent.submit(input().closest('form'));
}
const applied = { item_key: 'P1', updated: 1, delivery_status: 'applied' };

it.each([
  ['validation refusal', Object.assign(new Error('Invalid tag'), { status: 422 }), false],
  ['terminal native failure', Object.assign(new Error('Write failed'), { status: 500, body: { details: { delivery_status: 'failed' } } }), false],
  ['uncertain native delivery', Object.assign(new Error('Delivery unknown'), { status: 409, body: { details: { delivery_status: 'uncertain' } } }), true],
  ['lost response', new TypeError('Connection closed'), true],
  ['applied but refresh failed', Object.assign(new Error('Refresh failed'), { status: 500, body: { details: { delivery_status: 'applied' } } }), true],
])('retains the exact free-tag draft on %s without replay', async (_name, error, blocked) => {
  updateItemTags.mockRejectedValue(error);
  const { onChanged } = show();
  submit('  user’s typed tag  ');
  await screen.findByText(`Tagging failed: ${error.message}`);
  expect(input().value).toBe('  user’s typed tag  ');
  expect(onChanged).not.toHaveBeenCalled();
  expect(screen.getByRole('button', { name: 'Add', exact: true }).disabled).toBe(blocked);
  expect(updateItemTags).toHaveBeenCalledTimes(1);
  if (blocked) {
    fireEvent.submit(input().closest('form'));
    fireEvent.click(screen.getByRole('button', { name: '🧪 method' }));
    expect(updateItemTags).toHaveBeenCalledTimes(1);
    expect(screen.getByText(/Tag writes paused/)).toBeTruthy();
  } else {
    updateItemTags.mockResolvedValue(applied);
    fireEvent.submit(input().closest('form'));
    await waitFor(() => expect(input().value).toBe(''));
    expect(updateItemTags).toHaveBeenCalledTimes(2);
    expect(onChanged).toHaveBeenCalledTimes(1);
  }
});

it('retains the draft when explicit force is declined', async () => {
  updateItemTags.mockResolvedValue({ requires_force: true });
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
  const { onChanged } = show();
  submit('  Keep this tag  ');
  await waitFor(() => expect(confirm).toHaveBeenCalledTimes(1));
  expect(input().value).toBe('  Keep this tag  ');
  expect(updateItemTags).toHaveBeenCalledTimes(1);
  expect(onChanged).not.toHaveBeenCalled();
});

it.each([applied, { item_key: 'P1', updated: 0, item: { key: 'P1', tags: ['Already set'] } }])(
  'clears only after a same-item authoritative receipt: %j', async (receipt) => {
    let release;
    updateItemTags.mockReturnValue(new Promise(resolve => { release = resolve; }));
    const { onChanged } = show();
    submit('  Already set  ');
    expect(input().value).toBe('  Already set  ');
    fireEvent.submit(input().closest('form'));
    fireEvent.click(screen.getByRole('button', { name: '🧪 method' }));
    fireEvent.click(screen.getByRole('button', { name: 'Remove tag Saved tag' }));
    expect(updateItemTags).toHaveBeenCalledTimes(1);
    expect(updateItemTags).toHaveBeenCalledWith('P1', { addTags: ['Already set'], removeTags: [], force: false });
    await act(async () => { release(receipt); });
    expect(input().value).toBe('');
    expect(onChanged).toHaveBeenCalledTimes(1);
  },
);

it('keeps newer draft text while the explicitly forced captured tag is applied', async () => {
  let release;
  updateItemTags.mockResolvedValueOnce({ requires_force: true })
    .mockReturnValueOnce(new Promise(resolve => { release = resolve; }));
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  const { onChanged } = show();
  submit('  Original tag  ');
  await waitFor(() => expect(updateItemTags).toHaveBeenCalledTimes(2));
  expect(updateItemTags.mock.calls[1]).toEqual(['P1', { addTags: ['Original tag'], removeTags: [], force: true }]);
  expect(input().value).toBe('  Original tag  ');
  fireEvent.change(input(), { target: { value: 'Newer unsaved tag' } });
  await act(async () => { release(applied); });
  expect(input().value).toBe('Newer unsaved tag');
  expect(onChanged).toHaveBeenCalledTimes(1);
});

it.each([
  { item_key: 'P2', updated: 1, delivery_status: 'applied' },
  { item_key: 'P1', updated: 0, delivery_status: 'uncertain' },
  null,
])('retains intent and refuses an unconfirmed/foreign receipt: %j', async (receipt) => {
  updateItemTags.mockResolvedValue(receipt);
  const { onChanged } = show();
  submit('Unconfirmed tag');
  await screen.findByText('Tag delivery was not confirmed.');
  expect(input().value).toBe('Unconfirmed tag');
  expect(onChanged).not.toHaveBeenCalled();
  fireEvent.submit(input().closest('form'));
  expect(updateItemTags).toHaveBeenCalledTimes(1);
});

it.each(['applied', 'requires_force', 'error'])('fences a retired paper’s late %s even after returning to the same key', async (outcome) => {
  let resolveOld;
  let rejectOld;
  let resolveNew;
  updateItemTags.mockReturnValueOnce(new Promise((resolve, reject) => { resolveOld = resolve; rejectOld = reject; }))
    .mockReturnValueOnce(new Promise(resolve => { resolveNew = resolve; }));
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
  const { changePaper, onChanged } = show();
  submit('Old owner tag');
  changePaper('P2');
  expect(input().value).toBe('');
  changePaper('P1');
  submit('New owner tag');
  await act(async () => {
    if (outcome === 'error') rejectOld(new Error('Old failure'));
    else resolveOld(outcome === 'applied' ? applied : { requires_force: true });
  });
  expect(input().value).toBe('New owner tag');
  expect(screen.queryByText(/Old failure/)).toBeNull();
  expect(confirm).not.toHaveBeenCalled();
  expect(onChanged).not.toHaveBeenCalled();
  expect(updateItemTags).toHaveBeenCalledTimes(2);
  expect(screen.getByRole('button', { name: 'Add', exact: true }).disabled).toBe(true);
  await act(async () => { resolveNew(applied); });
  expect(input().value).toBe('');
  expect(onChanged).toHaveBeenCalledTimes(1);
});
