// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { UNDO_WINDOW_MS, decide } from './verdictUndo.js';

const api = vi.hoisted(() => ({ submitVerdict: vi.fn(), deleteVerdict: vi.fn(), queueRejectTag: vi.fn() }));
vi.mock('../api/goldenApi.js', () => ({ submitVerdict: api.submitVerdict, deleteVerdict: api.deleteVerdict }));
vi.mock('../api/libraryApi.js', () => ({ queueRejectTag: api.queueRejectTag }));

beforeEach(() => {
  vi.useFakeTimers();
  api.submitVerdict.mockResolvedValue({ verdict: {} });
  api.deleteVerdict.mockResolvedValue({ deleted: true });
  api.queueRejectTag.mockResolvedValue({});
});
afterEach(async () => {
  await vi.advanceTimersByTimeAsync(UNDO_WINDOW_MS);  // close any window a test left open
  vi.useRealTimers();
  vi.clearAllMocks();
});

it('runs Undo after its own in-flight POST so no verdict is left behind', async () => {
  const order = [];
  let finishPost;
  api.submitVerdict.mockImplementationOnce(() => new Promise((resolve) => {
    finishPost = () => { order.push('post'); resolve({ verdict: {} }); };
  }));
  api.deleteVerdict.mockImplementationOnce(async () => { order.push('delete'); return { deleted: true }; });
  const handle = decide('A', 'must_read', { prior: null });
  const undone = handle.undo();
  await vi.advanceTimersByTimeAsync(0);
  expect(api.deleteVerdict).not.toHaveBeenCalled();
  finishPost();
  await undone;
  expect(order).toEqual(['post', 'delete']);
});

it('restores the prior verdict with its comment when an overwrite is undone', async () => {
  const handle = decide('A', 'must_read', { prior: { user_priority: 'should_read', comment: 'because' }, surface: 'library_sheet' });
  await handle.undo();
  expect(api.submitVerdict).toHaveBeenLastCalledWith(
    { item_key: 'A', user_priority: 'should_read', comment: 'because', surface: 'library_sheet' });
  expect(api.deleteVerdict).not.toHaveBeenCalled();
});

it('does not queue the ❌ tag when Not interested is undone', async () => {
  const handle = decide('A', 'dont_read');
  await handle.undo();
  await vi.advanceTimersByTimeAsync(UNDO_WINDOW_MS * 2);
  expect(api.queueRejectTag).not.toHaveBeenCalled();
});

it('queues the ❌ tag exactly once, only after the Undo window', async () => {
  decide('A', 'dont_read');
  await vi.advanceTimersByTimeAsync(UNDO_WINDOW_MS - 1);
  expect(api.queueRejectTag).not.toHaveBeenCalled();
  await vi.advanceTimersByTimeAsync(1);
  await vi.advanceTimersByTimeAsync(UNDO_WINDOW_MS);
  expect(api.queueRejectTag).toHaveBeenCalledOnce();
  expect(api.queueRejectTag).toHaveBeenCalledWith('A');
});

it('never queues the ❌ tag for a surface that opts out with rejectTag: false', async () => {
  decide('A', 'dont_read', { rejectTag: false });
  await vi.advanceTimersByTimeAsync(UNDO_WINDOW_MS * 2);
  expect(api.queueRejectTag).not.toHaveBeenCalled();
  expect(api.submitVerdict).toHaveBeenCalledTimes(1);
});

it('sends the tag early on the next verdict and on pagehide', async () => {
  decide('A', 'dont_read');
  decide('B', 'must_read');
  await vi.advanceTimersByTimeAsync(0);
  expect(api.queueRejectTag).toHaveBeenCalledWith('A');
  decide('C', 'dont_read');
  window.dispatchEvent(new Event('pagehide'));
  await vi.advanceTimersByTimeAsync(0);
  expect(api.queueRejectTag).toHaveBeenCalledWith('C');
});

it('skips the tag for an offline save and queues a delete when it is undone', async () => {
  api.submitVerdict.mockResolvedValueOnce({ saved_offline: true, queued: true });
  const handle = decide('A', 'dont_read');
  await vi.advanceTimersByTimeAsync(UNDO_WINDOW_MS);
  expect(api.queueRejectTag).not.toHaveBeenCalled();
  await handle.undo();
  expect(api.deleteVerdict).toHaveBeenCalledWith('A');
});

it('sends two reason chips as one extra POST when the window closes', async () => {
  const handle = decide('A', 'must_read', { surface: 'library_sheet' });
  handle.setReasons(['Strong method']);
  handle.setReasons(['Strong method', 'Useful dataset']);
  await vi.advanceTimersByTimeAsync(UNDO_WINDOW_MS - 1);
  expect(api.submitVerdict).toHaveBeenCalledTimes(1);
  await vi.advanceTimersByTimeAsync(1);
  expect(api.submitVerdict).toHaveBeenCalledTimes(2);
  expect(api.submitVerdict).toHaveBeenLastCalledWith(
    { item_key: 'A', user_priority: 'must_read', comment: 'Strong method, Useful dataset', surface: 'library_sheet' });
});

it('warns when the tag request fails and does not repeat the verdict', async () => {
  api.queueRejectTag.mockRejectedValueOnce(new Error('queue down'));
  const onWarning = vi.fn();
  decide('A', 'dont_read', { onWarning });
  await vi.advanceTimersByTimeAsync(UNDO_WINDOW_MS);
  expect(onWarning).toHaveBeenCalledWith('Verdict saved, but reject tag was not queued: queue down');
  expect(api.submitVerdict).toHaveBeenCalledTimes(1);
});

it('keeps the earlier windows of a batch open so one Undo can still take them all back', async () => {
  const first = decide('A', 'dont_read');
  decide('B', 'dont_read', { keepOpen: true });
  await vi.advanceTimersByTimeAsync(UNDO_WINDOW_MS - 1);
  await first.undo();
  await vi.advanceTimersByTimeAsync(UNDO_WINDOW_MS * 2);
  expect(api.queueRejectTag.mock.calls).toEqual([['B']]);  // A was undone inside its window; only B's ❌ went out
});
