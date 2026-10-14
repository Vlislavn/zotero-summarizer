// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';

const mocks = vi.hoisted(() => ({ saveReviewNote: vi.fn() }));
vi.mock('../../../api/goldenApi.js', () => ({ saveReviewNote: mocks.saveReviewNote }));
import ReviewNotes from './ReviewNotes.jsx';

beforeEach(() => { mocks.saveReviewNote.mockReset().mockResolvedValue({ saved: true }); });
afterEach(cleanup);

it.each([
  [true, false, null, 'P1', 'Zotero note delivery: acknowledged (revision 7).'],
  [false, true, 'target_unavailable', null, 'Zotero note delivery: pending (revision 7) — target_unavailable.'],
])('renders one composite delivery receipt for the question list (%s)', (written, pending, error, target, text) => {
  render(<ReviewNotes itemKey="P1" questions={[
    { submission_id: 'q1', question: 'First question' },
    { submission_id: 'q2', question: 'Second question' },
  ]} questionMirror={{ note_written: written, note_pending: pending, note_error: error,
    target_key: target, projection_revision: 7 }} />);
  expect(screen.getByText(text)).toBeTruthy();
  expect(screen.queryAllByText('Zotero delivery: unknown')).toHaveLength(0);
});

it('renders question rows separately and preserves dirty manual text across detail refreshes', () => {
  const firstQuestions = [{ submission_id: 'q1', question: 'What was the cohort size?', origin: 'typed' }];
  const { rerender } = render(
    <ReviewNotes itemKey="P1" initialNote="Server note" questions={firstQuestions} />,
  );
  const editor = screen.getByRole('textbox');
  fireEvent.change(editor, { target: { value: 'My unsaved manual draft' } });

  const refreshedQuestions = [
    ...firstQuestions,
    { submission_id: 'q2', question: 'Was there external validation?', origin: 'preset' },
  ];
  rerender(
    <ReviewNotes itemKey="P1" initialNote="Server note" questions={refreshedQuestions} />,
  );
  expect(screen.getByText('Was there external validation?')).toBeTruthy();
  expect(editor.value).toBe('My unsaved manual draft');

  rerender(
    <ReviewNotes itemKey="P1" initialNote="A concurrent refresh changed the server note" questions={refreshedQuestions} />,
  );
  expect(editor.value).toBe('My unsaved manual draft');
  expect(screen.getAllByRole('textbox')).toHaveLength(1);
});

it('shows a rejected local question honestly instead of promising another sync', () => {
  render(<ReviewNotes itemKey="P1" questions={[{ submission_id: 'q1', question: 'Rejected question',
    saved_offline: true, sync_status: 'rejected' }]} />);
  expect(screen.getByText('Saved on this device — sync rejected')).toBeTruthy();
  expect(screen.getByText('Zotero note delivery: unknown.')).toBeTruthy();
  expect(screen.queryByText(/waiting to sync/)).toBeNull();
});

it('refreshes clean manual text and resets the draft only on a new paper', () => {
  const { rerender } = render(<ReviewNotes itemKey="P1" initialNote="Old note" />);
  rerender(<ReviewNotes itemKey="P1" initialNote="Remote note" />);
  expect(screen.getByRole('textbox').value).toBe('Remote note');
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Dirty' } });
  rerender(<ReviewNotes itemKey="P2" initialNote="Another paper" />);
  expect(screen.getByRole('textbox').value).toBe('Another paper');
});

it('saves only the manual text and remains clean after the saved-detail refresh', async () => {
  const onSaved = vi.fn();
  const onDirtyChange = vi.fn();
  const props = { itemKey: 'P1', onSaved, onDirtyChange,
    questions: [{ submission_id: 'q1', question: 'A separate question' }] };
  const { rerender } = render(<ReviewNotes {...props} initialNote="Before" />);
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'My manual text' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save' }));

  await waitFor(() => expect(onSaved).toHaveBeenCalledOnce());
  expect(mocks.saveReviewNote).toHaveBeenCalledWith({ item_key: 'P1', note: 'My manual text' });
  rerender(<ReviewNotes {...props} initialNote="My manual text" />);
  expect(screen.getByRole('textbox').value).toBe('My manual text');
  expect(onDirtyChange).toHaveBeenLastCalledWith(false);
  expect(screen.getByRole('button', { name: 'Save' }).disabled).toBe(true);
});

it('keeps edits made during an in-flight manual save dirty', async () => {
  let complete;
  mocks.saveReviewNote.mockImplementation(() => new Promise((resolve) => { complete = resolve; }));
  const onDirtyChange = vi.fn();
  const { rerender } = render(<ReviewNotes itemKey="P1" initialNote="Before" onDirtyChange={onDirtyChange} />);
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Sent draft' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save' }));
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Newer draft' } });
  complete({ saved: true });
  await screen.findByText('Saved in the app.');
  rerender(<ReviewNotes itemKey="P1" initialNote="Sent draft" onDirtyChange={onDirtyChange} />);
  expect(screen.getByRole('textbox').value).toBe('Newer draft');
  expect(onDirtyChange).toHaveBeenLastCalledWith(true);
});
