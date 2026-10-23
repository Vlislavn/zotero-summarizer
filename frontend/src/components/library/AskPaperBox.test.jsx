// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';

const mocks = vi.hoisted(() => ({
  queueQuestionSubmission: vi.fn(), saveReviewQuestion: vi.fn(), askPaper: vi.fn(),
}));
vi.mock('../../offlineStore.js', () => ({ queueQuestionSubmission: mocks.queueQuestionSubmission }));
vi.mock('../../api/libraryApi.js', () => ({
  saveReviewQuestion: mocks.saveReviewQuestion, askPaper: mocks.askPaper,
}));

import AskPaperBox from './AskPaperBox.jsx';

let uuid = 0;
beforeEach(() => {
  vi.clearAllMocks();
  uuid = 0;
  vi.stubGlobal('crypto', { randomUUID: () => `00000000-0000-4000-8000-${String(++uuid).padStart(12, '0')}` });
  Object.defineProperty(navigator, 'onLine', { configurable: true, value: true });
  mocks.queueQuestionSubmission.mockResolvedValue({ saved_offline: true, queued: true });
  mocks.saveReviewQuestion.mockImplementation(async (submission) => ({
    question_saved: true, question_submission: { ...submission, submitted_at: '2026-10-08T12:00:00Z' },
  }));
  mocks.askPaper.mockResolvedValue({
    answer: 'The paper reports 120 participants.', question_saved: true,
    question_submission: { submitted_at: '2026-10-08T12:00:00Z' },
  });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

function submit(text) {
  fireEvent.change(screen.getByRole('textbox', { name: 'Question about this paper' }), {
    target: { value: text },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Ask' }));
}

describe('AskPaperBox durable question submission', () => {
  it.each([
    [false, true, 'target_unavailable', null, 'Zotero note delivery: pending (revision 7) — target_unavailable.'],
    [true, false, null, 'P1', 'Zotero note delivery: acknowledged (revision 7).'],
  ])('forwards the composite mirror receipt to My notes instead of retaining another delivery status (%s)', async (written, pending, error, target, text) => {
    mocks.saveReviewQuestion.mockImplementation(async (submission) => ({
      question_saved: true, question_submission: submission,
      question_mirror: { note_written: written, note_pending: pending, note_error: error,
        target_key: target, projection_revision: 7 },
    }));
    mocks.askPaper.mockRejectedValue(new Error('model unavailable'));
    const reportMirror = vi.fn();
    render(<AskPaperBox itemKey="P1" variant="rail" onMirrorStart={() => reportMirror} />);
    submit('A durable question');
    await screen.findByRole('alert');
    expect(screen.getByText('Question saved in the app — see My notes for Zotero delivery.')).toBeTruthy();
    expect(reportMirror).toHaveBeenCalledWith({ note_written: written, note_pending: pending,
      note_error: error, target_key: target, projection_revision: 7 });
    expect(screen.queryByText(text)).toBeNull();
  });

  it('commits the typed question before comment-save or QA requests and sends its UUID', async () => {
    const order = [];
    mocks.queueQuestionSubmission.mockImplementation(async (payload) => {
      order.push(['local', payload]);
      return { saved_offline: true, queued: true };
    });
    mocks.saveReviewQuestion.mockImplementation(async (payload) => {
      order.push(['comment', payload]);
      return { question_saved: true, question_submission: payload };
    });
    mocks.askPaper.mockImplementation(async (...args) => {
      order.push(['qa', ...args]);
      return { answer: 'Answer', question_saved: true };
    });

    render(<AskPaperBox itemKey="P1" variant="rail" />);
    submit(' What was the sample size? ');

    await waitFor(() => expect(mocks.askPaper).toHaveBeenCalledOnce());
    expect(order.map(([kind]) => kind)).toEqual(['local', 'comment', 'qa']);
    const submission = order[0][1];
    expect(submission).toEqual({
      submission_id: '00000000-0000-4000-8000-000000000001',
      item_key: 'P1', question: 'What was the sample size?', origin: 'typed', preset_id: null,
    });
    expect(order[1][1]).toEqual(submission);
    expect(order[2][1]).toBe('P1');
    expect(order[2][2]).toBe(submission.question);
    expect(order[2][3]).toMatchObject({
      submissionId: submission.submission_id, origin: 'typed', presetId: null,
    });
    expect(await screen.findByText('Answer')).toBeTruthy();
  });

  it('uses a stable preset id and a fresh UUID for each deliberate repeat', async () => {
    render(<AskPaperBox itemKey="P1" variant="rail" />);
    fireEvent.click(screen.getByRole('button', { name: 'Is there external validation?' }));
    await waitFor(() => expect(mocks.askPaper).toHaveBeenCalledOnce());

    submit('Is there external validation?');
    await waitFor(() => expect(mocks.askPaper).toHaveBeenCalledTimes(2));

    expect(mocks.queueQuestionSubmission.mock.calls.map(([row]) => row)).toEqual([
      { submission_id: '00000000-0000-4000-8000-000000000001', item_key: 'P1',
        question: 'Is there external validation?', origin: 'preset', preset_id: 'external_validation' },
      { submission_id: '00000000-0000-4000-8000-000000000002', item_key: 'P1',
        question: 'Is there external validation?', origin: 'typed', preset_id: null },
    ]);
  });

  it('keeps a saved question after QA failure and retries the stored intent with the same UUID', async () => {
    mocks.askPaper.mockRejectedValueOnce(new Error('model unavailable'))
      .mockResolvedValueOnce({ answer: 'Recovered', question_saved: true });
    render(<AskPaperBox itemKey="P1" variant="rail" />);
    submit('What was the sample size?');

    expect(await screen.findByText(/Ask failed: model unavailable/)).toBeTruthy();
    expect(mocks.queueQuestionSubmission).toHaveBeenCalledOnce();
    expect(mocks.saveReviewQuestion).toHaveBeenCalledOnce();
    fireEvent.change(screen.getByRole('textbox', { name: 'Question about this paper' }), {
      target: { value: 'A different draft' },
    });
    fireEvent.change(screen.getByRole('combobox', { name: 'Answer mode' }), { target: { value: 'retrieval' } });
    fireEvent.click(screen.getByRole('button', { name: 'Retry answer' }));

    await waitFor(() => expect(mocks.askPaper).toHaveBeenCalledTimes(2));
    expect(mocks.queueQuestionSubmission.mock.calls[0][0].submission_id)
      .toBe(mocks.queueQuestionSubmission.mock.calls[1][0].submission_id);
    expect(mocks.askPaper.mock.calls[0]).toEqual(mocks.askPaper.mock.calls[1]);
    expect(await screen.findByText('Recovered')).toBeTruthy();
  });

  it('does not start QA when the server comment cannot be confirmed', async () => {
    mocks.saveReviewQuestion.mockRejectedValue(new Error('server unavailable'));
    render(<AskPaperBox itemKey="P1" variant="rail" />);
    submit('Keep locally');

    expect(await screen.findByText(/Server comment not confirmed: server unavailable/)).toBeTruthy();
    expect(mocks.queueQuestionSubmission).toHaveBeenCalledOnce();
    expect(mocks.askPaper).not.toHaveBeenCalled();
    expect(screen.getByText('Saved on this device — server comment pending.')).toBeTruthy();
  });

  it.each([{ question_saved: false }, { question_saved: true }])('does not start QA for a malformed comment acknowledgement: %j', async (receipt) => {
    mocks.saveReviewQuestion.mockResolvedValue(receipt);
    render(<AskPaperBox itemKey="P1" variant="rail" />);
    submit('Keep locally');

    expect(await screen.findByText(/Server comment not confirmed/)).toBeTruthy();
    expect(mocks.askPaper).not.toHaveBeenCalled();
  });

  it.each(['x'.repeat(2001), '😀'.repeat(2001)])('rejects overlong questions before saving or network work', async (text) => {
    render(<AskPaperBox itemKey="P1" variant="rail" />);
    submit(text);

    expect(await screen.findByText(/Question must be nonblank text of at most 2000 characters/)).toBeTruthy();
    expect(mocks.queueQuestionSubmission).not.toHaveBeenCalled();
    expect(mocks.saveReviewQuestion).not.toHaveBeenCalled();
    expect(mocks.askPaper).not.toHaveBeenCalled();
  });

  it('stores offline questions locally and never starts comment or QA requests', async () => {
    Object.defineProperty(navigator, 'onLine', { configurable: true, value: false });
    render(<AskPaperBox itemKey="P1" variant="rail" />);
    submit('Offline question');

    expect(await screen.findByText(/Saved on this device/)).toBeTruthy();
    expect(mocks.queueQuestionSubmission).toHaveBeenCalledOnce();
    expect(mocks.saveReviewQuestion).not.toHaveBeenCalled();
    expect(mocks.askPaper).not.toHaveBeenCalled();
  });

  it('waits for the durable transaction and server confirmation before QA', async () => {
    let committed;
    let confirmed;
    mocks.queueQuestionSubmission.mockImplementation(() => new Promise((resolve) => { committed = resolve; }));
    mocks.saveReviewQuestion.mockImplementation(() => new Promise((resolve) => { confirmed = resolve; }));
    render(<AskPaperBox itemKey="P1" variant="rail" />);
    submit('Wait for the commit');
    expect(mocks.saveReviewQuestion).not.toHaveBeenCalled();
    expect(mocks.askPaper).not.toHaveBeenCalled();

    committed({ saved_offline: true });
    await waitFor(() => expect(mocks.saveReviewQuestion).toHaveBeenCalledOnce());
    expect(mocks.askPaper).not.toHaveBeenCalled();
    confirmed({ question_saved: true, question_submission: mocks.queueQuestionSubmission.mock.calls[0][0] });
    await waitFor(() => expect(mocks.askPaper).toHaveBeenCalledOnce());
  });

  it('refreshes the existing detail after comment success and QA failure without guessing mirror success', async () => {
    const onSaved = vi.fn();
    mocks.askPaper.mockRejectedValueOnce(new Error('model unavailable'));
    render(<AskPaperBox itemKey="P1" variant="rail" onSaved={onSaved} />);
    submit('Keep this question');
    await screen.findByText(/Ask failed: model unavailable/);
    expect(onSaved).toHaveBeenCalled();
    expect(screen.getByText('Question saved in the app — see My notes for Zotero delivery.')).toBeTruthy();
  });

  it('keeps abstention distinct from comment success', async () => {
    mocks.askPaper.mockResolvedValue({ answer: '', abstained: true, question_saved: true });
    render(<AskPaperBox itemKey="P1" variant="rail" />);
    submit('An unanswered question');
    expect(await screen.findByText(/the model abstained rather than guessing/)).toBeTruthy();
    expect(screen.getByText('Question saved in the app — see My notes for Zotero delivery.')).toBeTruthy();
  });

  it('does not put a late answer into another paper after navigation', async () => {
    let finish;
    mocks.askPaper.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    const { rerender } = render(<AskPaperBox itemKey="P1" variant="rail" />);
    submit('P1 question');
    await waitFor(() => expect(mocks.askPaper).toHaveBeenCalledOnce());
    rerender(<AskPaperBox itemKey="P2" variant="rail" />);
    finish({ answer: 'Late P1 answer' });
    await waitFor(() => expect(screen.getByRole('button', { name: 'Ask' }).textContent).toBe('Ask'));
    expect(screen.queryByText('Late P1 answer')).toBeNull();
    expect(screen.getByRole('textbox', { name: 'Question about this paper' }).value).toBe('');
  });

  it('does not start network work when the local outbox commit fails', async () => {
    mocks.queueQuestionSubmission.mockRejectedValue(new Error('IndexedDB full'));
    render(<AskPaperBox itemKey="P1" variant="rail" />);
    submit('Must not disappear');

    expect(await screen.findByText(/Question not saved on this device: IndexedDB full/)).toBeTruthy();
    expect(mocks.saveReviewQuestion).not.toHaveBeenCalled();
    expect(mocks.askPaper).not.toHaveBeenCalled();
  });
});
