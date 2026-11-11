// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import DeepReviewSection from './DeepReviewSection.jsx';
import useDeepReviewRunner from '../../hooks/useDeepReviewRunner.js';

vi.mock('../../hooks/useDeepReviewRunner.js', () => ({ default: vi.fn() }));
beforeEach(() => useDeepReviewRunner.mockReturnValue({
  status: { status: 'error', error: 'Original technical failure', diagnostic: {
    code: 'source_unusable', stage: 'source_admission', recovery: 'Provide an original paper PDF',
  } }, online: true, llmAvailable: true, llm: {}, running: false, run: vi.fn(),
}));
afterEach(() => { cleanup(); vi.resetAllMocks(); });

it('shows typed recovery and retains original technical error in a disclosure', () => {
  render(<DeepReviewSection itemKey="A" />);
  expect(screen.getByText('Provide an original paper PDF')).toBeTruthy();
  expect(screen.getByText('source_admission / source_unusable')).toBeTruthy();
  expect(screen.getByText('Original technical failure')).toBeTruthy();
});

it('blocks generation while an uncached status read is pending', () => {
  const run = vi.fn();
  useDeepReviewRunner.mockReturnValue({ status: { status: 'idle' }, online: true,
    llmAvailable: true, llm: {}, running: false, checkingStatus: true, run });
  render(<DeepReviewSection itemKey="A" compact />);
  const generate = screen.getByRole('button', { name: 'Run deeper review' });
  expect(generate.disabled).toBe(true);
  fireEvent.click(generate);
  expect(run).not.toHaveBeenCalled();
});

it.each([true, false])('keeps explicit status recovery outside compact=%s generation controls', (compact) => {
  const runner = { status: { status: 'unavailable', error: 'HTTP 503' }, online: true,
    llmAvailable: true, llm: {}, running: false, checkingStatus: false,
    run: vi.fn(), retryStatus: vi.fn() };
  useDeepReviewRunner.mockReturnValue(runner);
  const deep = { digest: { tldr: 'Retained compact body' } };
  const { rerender } = render(<DeepReviewSection itemKey="A" deep={deep} compact={compact} />);
  expect(screen.getByText('Retained compact body')).toBeTruthy();
  expect(screen.queryByText(/Deep review failed/)).toBeNull();
  const generate = screen.queryByRole('button', { name: 'Re-run deeper review' });
  if (compact) expect(generate).toBeNull();
  else {
    expect(generate.disabled).toBe(true);
    fireEvent.click(generate);
  }
  fireEvent.click(screen.getByRole('button', { name: 'Retry status' }));
  expect(runner.retryStatus).toHaveBeenCalledOnce();
  expect(runner.run).not.toHaveBeenCalled();

  useDeepReviewRunner.mockReturnValue({ ...runner, checkingStatus: true });
  rerender(<DeepReviewSection itemKey="A" deep={deep} compact={compact} />);
  const retry = screen.getByRole('button', { name: 'Retry status' });
  expect(retry.disabled).toBe(true);
  fireEvent.click(retry);
  expect(runner.retryStatus).toHaveBeenCalledOnce();
});
