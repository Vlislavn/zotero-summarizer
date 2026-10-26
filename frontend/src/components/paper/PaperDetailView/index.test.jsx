// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';

const mocks = vi.hoisted(() => ({ saveReviewNote: vi.fn(), queueQuestionSubmission: vi.fn(),
  saveReviewQuestion: vi.fn(), askPaper: vi.fn(), isServerAvailable: vi.fn() }));
vi.mock('../../../api/goldenApi.js', () => ({ saveReviewNote: mocks.saveReviewNote }));
vi.mock('../../../api/libraryApi.js', () => ({ saveReviewQuestion: mocks.saveReviewQuestion, askPaper: mocks.askPaper }));
vi.mock('../../../offlineStore.js', () => ({ queueQuestionSubmission: mocks.queueQuestionSubmission,
  isServerAvailable: mocks.isServerAvailable, syncStatusEvent: 'zs-sync-status' }));
vi.mock('../LinksRow.jsx', () => ({ default: () => null }));
vi.mock('../DeepReviewSection.jsx', () => ({ default: () => null }));
vi.mock('../TagOfInterestEditor.jsx', () => ({ default: () => null }));
vi.mock('../CollectionEditor.jsx', () => ({ default: () => null }));
vi.mock('../../library/PaperReaderPane.jsx', () => ({ default: () => null }));
vi.mock('../../library/OpenBriefButton.jsx', () => ({ default: () => null }));
vi.mock('../../VerdictPanel.jsx', () => ({ default: () => null }));
import PaperDetailView from './index.jsx';

beforeEach(() => {
  vi.clearAllMocks();
  mocks.isServerAvailable.mockReturnValue(true);
  mocks.saveReviewNote.mockResolvedValue({ saved: true });
  mocks.queueQuestionSubmission.mockResolvedValue({ saved_offline: true });
  mocks.saveReviewQuestion.mockImplementation(async (row) => ({ question_saved: true, question_submission: row }));
  mocks.askPaper.mockResolvedValue({ answer: 'Answer' });
  Object.defineProperty(navigator, 'onLine', { configurable: true, value: true });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it.each(['editable', 'readonly'])('wires shared %s notes to detail questions and the existing refresh callback', async (mode) => {
  const onDeepReviewDone = vi.fn();
  const detail = { has_pdf: true, user_note: 'Manual note', questions: [{ submission_id: 'q1', question: 'Stored question' }] };
  const props = { mode, itemKey: 'P1', compact: true, onDeepReviewDone };
  const { rerender } = render(<PaperDetailView {...props} detail={detail} />);
  expect(screen.getByText('Stored question')).toBeTruthy();
  fireEvent.change(screen.getByRole('textbox', { name: 'Manual notes' }), { target: { value: 'Unsaved draft' } });
  rerender(<PaperDetailView {...props} detail={{ ...detail, user_note: 'Concurrent note',
    questions: [...detail.questions, { submission_id: 'q2', question: 'New question' }] }} />);
  expect(screen.getByText('New question')).toBeTruthy();
  expect(screen.getByRole('textbox', { name: 'Manual notes' }).value).toBe('Unsaved draft');

  fireEvent.click(screen.getByRole('button', { name: 'Save' }));
  await waitFor(() => expect(onDeepReviewDone).toHaveBeenCalledOnce());
  expect(mocks.saveReviewNote).toHaveBeenCalledWith({ item_key: 'P1', note: 'Unsaved draft' });
  fireEvent.change(screen.getByRole('textbox', { name: 'Question about this paper' }), { target: { value: 'Submitted question' } });
  fireEvent.click(screen.getByRole('button', { name: 'Ask' }));
  await screen.findByText('Answer');
  expect(onDeepReviewDone.mock.calls.length).toBeGreaterThan(1);
});

it('keeps My notes available without PDF, review or Add while preserving the Ask/PDF gate', () => {
  render(<PaperDetailView mode="editable" itemKey="feed:group:paper" detail={{ has_pdf: false, user_note: '',
    questions: [{ submission_id: 'q1', question: 'A question saved before a failed review' }] }} />);
  expect(screen.getByText('My notes')).toBeTruthy();
  expect(screen.getByText('A question saved before a failed review')).toBeTruthy();
  expect(screen.queryByRole('button', { name: 'Ask' })).toBeNull();
});

it('exposes compact Ask in one disclosure and refreshes questions without replacing manual text', async () => {
  mocks.askPaper.mockRejectedValueOnce(new Error('model unavailable'));
  const onDeepReviewDone = vi.fn();
  const detail = { has_pdf: true, user_note: 'Saved manual note', questions: [] };
  const props = { itemKey: 'P1', mode: 'editable', compact: true, onDeepReviewDone,
    show: { reader: false, abstract: false, links: false, tags: false, collection: false, verdict: false } };
  const { container, rerender } = render(<PaperDetailView {...props} detail={detail} />);
  const question = container.querySelector('[aria-label="Question about this paper"]');
  const disclosure = question.closest('details');
  expect(disclosure.parentElement.closest('details')).toBeNull();
  fireEvent.click(disclosure.querySelector('summary'));
  fireEvent.change(screen.getByRole('textbox', { name: 'Manual notes' }), { target: { value: 'Dirty manual note' } });
  fireEvent.change(question, { target: { value: 'Question preserved despite QA failure' } });
  fireEvent.click(screen.getByRole('button', { name: 'Ask' }));
  await screen.findByText('Ask failed: model unavailable');
  await waitFor(() => expect(onDeepReviewDone).toHaveBeenCalled());
  const submission = mocks.queueQuestionSubmission.mock.calls[0][0];

  rerender(<PaperDetailView {...props} detail={{ ...detail, questions: [submission] }} />);

  expect(screen.getByRole('list', { name: 'Saved review questions' }).textContent).toContain(submission.question);
  expect(screen.getByRole('textbox', { name: 'Manual notes' }).value).toBe('Dirty manual note');
  expect(mocks.askPaper).toHaveBeenCalledOnce();
});

it.each(['editable', 'readonly'])('keeps the %s question draft owner across actual availability notifications', (mode) => {
  const props = { mode, itemKey: 'P1', detail: { has_pdf: true, user_note: '', questions: [] },
    show: { reader: false, abstract: false, links: false, tags: false, collection: false, verdict: false } };
  const { container } = render(<PaperDetailView {...props} />);
  const input = container.querySelector('[aria-label="Question about this paper"]');
  expect(input).not.toBeNull();
  fireEvent.change(input, { target: { value: 'Draft survives a sync notification' } });
  mocks.isServerAvailable.mockReturnValue(false);
  fireEvent(window, new Event('zs-sync-status'));
  expect(container.querySelector('[aria-label="Question about this paper"]')).toBe(input);
  expect(input.value).toBe('Draft survives a sync notification');
  mocks.isServerAvailable.mockReturnValue(true);
  fireEvent(window, new Event('zs-sync-status'));
  expect(container.querySelector('[aria-label="Question about this paper"]')).toBe(input);
  expect(input.value).toBe('Draft survives a sync notification');
  expect(mocks.queueQuestionSubmission).not.toHaveBeenCalled();
  expect(mocks.saveReviewQuestion).not.toHaveBeenCalled();
  expect(mocks.askPaper).not.toHaveBeenCalled();
});
