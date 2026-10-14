// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';

const mocks = vi.hoisted(() => ({ queueQuestionSubmission: vi.fn(), saveReviewQuestion: vi.fn(), askPaper: vi.fn() }));
vi.mock('../../../offlineStore.js', () => ({ queueQuestionSubmission: mocks.queueQuestionSubmission }));
vi.mock('../../../api/libraryApi.js', () => ({ saveReviewQuestion: mocks.saveReviewQuestion, askPaper: mocks.askPaper }));
vi.mock('../../../api/goldenApi.js', () => ({ saveReviewNote: vi.fn() }));
vi.mock('../../VerdictPanel.jsx', () => ({ default: () => null }));
vi.mock('../CollectionEditor.jsx', () => ({ default: () => null }));
vi.mock('../TagOfInterestEditor.jsx', () => ({ default: () => null }));
import ActionRail from './ActionRail.jsx';
afterEach(cleanup);

it('uses separate questions and refreshes the full-page notes on Ask failure without a PDF/review/Add precondition', async () => {
  mocks.queueQuestionSubmission.mockResolvedValue({ saved_offline: true });
  mocks.saveReviewQuestion.mockImplementation(async (row) => ({ question_saved: true, question_submission: row }));
  mocks.askPaper.mockRejectedValue(new Error('missing paper text'));
  Object.defineProperty(navigator, 'onLine', { configurable: true, value: true });
  const onNoteSaved = vi.fn();
  render(<ActionRail itemKey="feed:group:paper" hasPdf={false} canAsk collections={[]} verdict={{}}
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
