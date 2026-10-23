// @vitest-environment jsdom
import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import useNoteDelivery, { noteDeliveryText } from './useNoteDelivery.js';

afterEach(cleanup);
const question = { submission_id: 'q1', item_key: 'P1', question: 'First question', origin: 'typed', preset_id: null };
const acknowledged = { note_written: true, note_pending: false, note_error: null, target_key: 'P1', projection_revision: 7 };
const initial = { key: 'P1', note: 'Manual note', questions: [question] };
function useFixture(props) { return useNoteDelivery(props.key, props.note, props.questions); }
function begin(result, options) {
  let complete;
  act(() => { complete = result.current.beginNoteDelivery(options); });
  return complete;
}
function deliver(result, receipt, options) {
  const complete = begin(result, options);
  act(() => complete(receipt));
}

it('binds the supplied receipt to the saved questions, not the prior detail', () => {
  const { result, rerender } = renderHook(useFixture, { initialProps: initial });
  const next = { ...question, submission_id: 'q2', question: 'Next question' };
  deliver(result, acknowledged, { question: next });
  expect(result.current.questionMirror).toBeNull();
  rerender({ ...initial, questions: [question, next] });
  expect(result.current.questionMirror).toEqual(acknowledged);
  rerender({ ...initial, questions: [next, question] });
  expect(result.current.questionMirror).toEqual(acknowledged);
});

it('invalidates an acknowledgement after a new offline question without reviving it later', () => {
  const { result, rerender } = renderHook(useFixture, { initialProps: initial });
  deliver(result, acknowledged);
  expect(result.current.questionMirror).toEqual(acknowledged);
  rerender({ ...initial, questions: [question, { ...question, submission_id: 'q2', saved_offline: true }] });
  expect(result.current.questionMirror).toBeNull();
  rerender(initial);
  expect(result.current.questionMirror).toBeNull();
});

it('invalidates a receipt for changed immutable payload even when UUID stays the same', () => {
  const { result, rerender } = renderHook(useFixture, { initialProps: initial });
  deliver(result, acknowledged);
  rerender({ ...initial, questions: [{ ...question, question: 'Incompatible same UUID' }] });
  expect(result.current.questionMirror).toBeNull();
});

it('clears delivery when a new submission begins before a device commit or HTTP response', () => {
  const { result } = renderHook(useFixture, { initialProps: initial });
  deliver(result, acknowledged);
  begin(result);
  expect(result.current.questionMirror).toBeNull();
});

it('binds a manual-save receipt to the saved text and invalidates it on later changes', () => {
  const { result, rerender } = renderHook(useFixture, { initialProps: initial });
  deliver(result, acknowledged, { note: 'Saved text' });
  expect(result.current.questionMirror).toBeNull();
  rerender({ ...initial, note: 'Saved text' });
  expect(result.current.questionMirror).toEqual(acknowledged);
  rerender({ ...initial, note: 'Newer server text' });
  expect(result.current.questionMirror).toBeNull();
  rerender({ ...initial, note: 'Saved text' });
  expect(result.current.questionMirror).toBeNull();
});

it('refuses a late receipt after navigation or a different server snapshot', () => {
  const { result, rerender } = renderHook(useFixture, { initialProps: initial });
  const oldCallback = begin(result);
  rerender({ ...initial, key: 'P2' });
  act(() => oldCallback(acknowledged));
  expect(result.current.questionMirror).toBeNull();
  rerender(initial);
  const beforeRefresh = begin(result);
  rerender({ ...initial, note: 'Changed during request' });
  act(() => beforeRefresh(acknowledged));
  expect(result.current.questionMirror).toBeNull();
});

it.each([
  null, {}, { ...acknowledged, note_written: 1 }, { ...acknowledged, projection_revision: true },
  { ...acknowledged, note_pending: 'false' }, { ...acknowledged, target_key: null },
])('never fabricates delivery from an unavailable or malformed receipt: %j', (receipt) => {
  expect(noteDeliveryText(receipt)).toBe('Zotero note delivery: unknown.');
});

it('recognizes the already-acknowledged no-op receipt without another external write', () => {
  expect(noteDeliveryText({ ...acknowledged, note_written: false }))
    .toBe('Zotero note delivery: acknowledged (revision 7).');
  expect(noteDeliveryText({ ...acknowledged, note_written: false, projection_revision: 0 }))
    .toBe('Zotero note delivery: unknown.');
});

it('retires a delayed acknowledgement after a newer request clears delivery', () => {
  const { result } = renderHook(useFixture, { initialProps: initial });
  const oldCallback = begin(result);
  begin(result);
  act(() => oldCallback(acknowledged));
  expect(result.current.questionMirror).toBeNull();
});

it('retires a delayed acknowledgement after changed content returns to the same text', () => {
  const { result, rerender } = renderHook(useFixture, { initialProps: initial });
  const oldCallback = begin(result);
  rerender({ ...initial, note: 'Newer saved text' });
  rerender(initial);
  act(() => oldCallback(acknowledged));
  expect(result.current.questionMirror).toBeNull();
});

it('allows its expected submitted snapshot to arrive before the completion', () => {
  const { result, rerender } = renderHook(useFixture, { initialProps: initial });
  const complete = begin(result, { note: 'Saved text' });
  rerender({ ...initial, note: 'Saved text' });
  act(() => complete(acknowledged));
  expect(result.current.questionMirror).toEqual(acknowledged);
});

it('does not let an obsolete completion clear a newer acknowledgement', () => {
  const { result } = renderHook(useFixture, { initialProps: initial });
  const old = begin(result);
  const complete = begin(result);
  const newer = { ...acknowledged, projection_revision: 8 };
  act(() => complete(newer));
  act(() => old({ ...acknowledged, note_pending: true }));
  expect(result.current.questionMirror).toEqual(newer);
});

it('does not revive an old completion after navigating away and back', () => {
  const { result, rerender } = renderHook(useFixture, { initialProps: initial });
  const old = begin(result);
  rerender({ ...initial, key: 'P2' });
  rerender(initial);
  act(() => old(acknowledged));
  expect(result.current.questionMirror).toBeNull();
});

it('keeps pending and failed composite outcomes distinct from acknowledgement', () => {
  expect(noteDeliveryText({ ...acknowledged, note_written: false, note_pending: true, note_error: 'target_unavailable' }))
    .toBe('Zotero note delivery: pending (revision 7) — target_unavailable.');
  expect(noteDeliveryText({ ...acknowledged, note_written: false, note_error: 'write_failed' }))
    .toBe('Zotero note delivery: not confirmed (revision 7) — write_failed.');
});
