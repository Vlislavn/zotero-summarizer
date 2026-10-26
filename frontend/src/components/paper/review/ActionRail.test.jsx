// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';

const mocks = vi.hoisted(() => ({ queueQuestionSubmission: vi.fn(), saveReviewQuestion: vi.fn(), askPaper: vi.fn() }));
vi.mock('../../../offlineStore.js', () => ({ queueQuestionSubmission: mocks.queueQuestionSubmission }));
vi.mock('../../../api/libraryApi.js', () => ({ saveReviewQuestion: mocks.saveReviewQuestion, askPaper: mocks.askPaper }));
vi.mock('../../../api/goldenApi.js', () => ({ saveReviewNote: vi.fn() }));
vi.mock('../../VerdictPanel.jsx', () => ({ default: () => null }));
vi.mock('../CollectionEditor.jsx', () => ({ default: () => null }));
vi.mock('../TagOfInterestEditor.jsx', () => ({ default: () => null }));
import ActionRail from './ActionRail.jsx';
beforeEach(() => vi.resetAllMocks());
afterEach(cleanup);

it('uses separate questions and refreshes the full-page notes on Ask failure without a PDF/review/Add precondition', async () => {
  mocks.queueQuestionSubmission.mockResolvedValue({ saved_offline: true });
  mocks.saveReviewQuestion.mockImplementation(async (row) => ({ question_saved: true, question_submission: row }));
  mocks.askPaper.mockRejectedValue(new Error('missing paper text'));
  Object.defineProperty(navigator, 'onLine', { configurable: true, value: true });
  const onNoteSaved = vi.fn();
  render(<ActionRail itemKey="feed:group:paper" hasPdf={false} collections={[]} verdict={{}}
    onNoteSaved={onNoteSaved} detail={{ user_note: 'Manual note', questions: [{ submission_id: 'q1', question: 'Separate question' }] }} />);
  expect(screen.getByText('Separate question')).toBeTruthy();
  expect(screen.getByRole('textbox', { name: 'Manual notes' }).value).toBe('Manual note');
  expect(screen.queryByRole('combobox', { name: 'Answer mode' })).toBeNull();
  fireEvent.change(screen.getByRole('textbox', { name: 'Question about this paper' }), { target: { value: 'Another question' } });
  fireEvent.click(screen.getByRole('button', { name: 'Ask' }));
  await screen.findByText('Ask failed: missing paper text');
  expect(onNoteSaved).toHaveBeenCalled();
  expect(mocks.queueQuestionSubmission).toHaveBeenCalledOnce();
});

it('keeps one current delivery status in My notes and retires it without overwriting a dirty editor', async () => {
  const receipt = { note_written: true, note_pending: false, note_error: null,
    target_key: 'P1', projection_revision: 7 };
  mocks.queueQuestionSubmission.mockResolvedValue({ saved_offline: true });
  mocks.saveReviewQuestion.mockImplementation(async (row) => ({
    question_saved: true, question_submission: row, question_mirror: receipt,
  }));
  mocks.askPaper.mockRejectedValue(new Error('model unavailable'));
  Object.defineProperty(navigator, 'onLine', { configurable: true, value: true });
  const props = { itemKey: 'P1', hasPdf: true, collections: [], verdict: {} };
  const { rerender } = render(<ActionRail {...props} detail={{ user_note: 'Manual note', questions: [] }} />);
  fireEvent.change(screen.getByRole('textbox', { name: 'Manual notes' }), { target: { value: 'Unsaved draft' } });
  fireEvent.change(screen.getByRole('textbox', { name: 'Question about this paper' }), { target: { value: 'Preserve this question' } });
  fireEvent.click(screen.getByRole('button', { name: 'Ask' }));
  await screen.findByText('Ask failed: model unavailable');
  const submitted = mocks.queueQuestionSubmission.mock.calls[0][0];
  rerender(<ActionRail {...props} detail={{ user_note: 'Manual note', questions: [submitted] }} />);

  await waitFor(() => expect(screen.getAllByText(/Zotero note delivery: acknowledged/)).toHaveLength(1));
  expect(screen.getByRole('list', { name: 'Saved review questions' }).textContent).toContain(submitted.question);
  rerender(<ActionRail {...props} detail={{ user_note: 'Concurrent saved note', questions: [submitted] }} />);
  expect(screen.queryByText(/Zotero note delivery: acknowledged/)).toBeNull();
  expect(screen.getByText('Zotero note delivery: unknown.')).toBeTruthy();
  expect(screen.getByRole('textbox', { name: 'Manual notes' }).value).toBe('Unsaved draft');
});

it('keeps the question owner through syncing and binds one held acknowledgement after its expected snapshot', async () => {
  const receipt = { note_written: true, note_pending: false, note_error: null,
    target_key: 'P1', projection_revision: 9 };
  let completeSave;
  mocks.queueQuestionSubmission.mockResolvedValue({ saved_offline: true });
  mocks.saveReviewQuestion.mockImplementation((row) => new Promise((resolve) => {
    completeSave = () => resolve({ question_saved: true, question_submission: row, question_mirror: receipt });
  }));
  mocks.askPaper.mockRejectedValue(new Error('controlled answer refusal'));
  Object.defineProperty(navigator, 'onLine', { configurable: true, value: true });
  const detail = { user_note: 'Manual note', questions: [] };
  const props = { itemKey: 'P1', hasPdf: true, collections: [], verdict: {} };
  const { container, rerender } = render(<ActionRail {...props} serverAvailable detail={detail} />);
  const input = screen.getByRole('textbox', { name: 'Question about this paper' });
  fireEvent.change(screen.getByRole('textbox', { name: 'Manual notes' }), { target: { value: 'Dirty manual draft' } });
  fireEvent.change(input, { target: { value: 'Hold this question across sync' } });
  fireEvent.click(screen.getByRole('button', { name: 'Ask' }));
  await waitFor(() => expect(mocks.saveReviewQuestion).toHaveBeenCalledOnce());
  const submission = mocks.queueQuestionSubmission.mock.calls[0][0];
  const expected = { ...detail, questions: [submission] };
  try {
    rerender(<ActionRail {...props} serverAvailable={false} detail={expected} />);
    expect(screen.getByRole('textbox', { name: 'Question about this paper' })).toBe(input);
    expect(container.querySelector('fieldset').disabled).toBe(true);
    expect(mocks.askPaper).not.toHaveBeenCalled();
    completeSave();
    await screen.findByText('Ask failed: controlled answer refusal');
    await waitFor(() => expect(screen.getAllByText(/Zotero note delivery: acknowledged/)).toHaveLength(1));
    rerender(<ActionRail {...props} serverAvailable detail={expected} />);
    expect(screen.getByRole('textbox', { name: 'Question about this paper' })).toBe(input);
    expect(screen.getByRole('textbox', { name: 'Manual notes' }).value).toBe('Dirty manual draft');
    expect(mocks.askPaper).toHaveBeenCalledOnce();
    expect(mocks.saveReviewQuestion).toHaveBeenCalledOnce();
  } finally {
    completeSave();
  }
});

it('keeps offline capture without automatic server replay and retains source eligibility', async () => {
  mocks.queueQuestionSubmission.mockResolvedValue({ saved_offline: true });
  mocks.saveReviewQuestion.mockImplementation(async (row) => ({ question_saved: true, question_submission: row }));
  mocks.askPaper.mockResolvedValue({ answer: 'Controlled answer' });
  Object.defineProperty(navigator, 'onLine', { configurable: true, value: false });
  const props = { itemKey: 'P1', hasPdf: true, collections: [], verdict: {}, detail: { user_note: '', questions: [] } };
  const { rerender } = render(<ActionRail {...props} serverAvailable={false} />);
  const input = screen.getByRole('textbox', { name: 'Question about this paper' });
  fireEvent.change(input, { target: { value: 'Capture while disconnected' } });
  fireEvent.click(screen.getByRole('button', { name: 'Ask' }));
  await screen.findByText('Saved on this device — server comment pending.');
  const submission = mocks.queueQuestionSubmission.mock.calls[0][0];
  expect(mocks.saveReviewQuestion).not.toHaveBeenCalled();
  expect(mocks.askPaper).not.toHaveBeenCalled();
  Object.defineProperty(navigator, 'onLine', { configurable: true, value: true });
  rerender(<ActionRail {...props} serverAvailable />);
  expect(screen.getByRole('textbox', { name: 'Question about this paper' })).toBe(input);
  expect(mocks.saveReviewQuestion).not.toHaveBeenCalled();
  expect(mocks.askPaper).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Retry answer' }));
  await screen.findByText('Controlled answer');
  expect(mocks.saveReviewQuestion).toHaveBeenCalledOnce();
  expect(mocks.saveReviewQuestion).toHaveBeenCalledWith(submission);
  expect(mocks.queueQuestionSubmission.mock.calls[1][0]).toEqual(submission);
  rerender(<ActionRail {...props} hasPdf={false} serverAvailable />);
  expect(screen.getByRole('textbox', { name: 'Question about this paper' })).toBe(input);
  expect(screen.queryByRole('combobox', { name: 'Answer mode' })).toBeNull();
  expect(mocks.saveReviewQuestion).toHaveBeenCalledOnce();
  expect(mocks.askPaper).toHaveBeenCalledOnce();
});
