// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import DeepReviewSection from './DeepReviewSection.jsx';

vi.mock('../../hooks/useDeepReviewRunner.js', () => ({ default: () => ({
  status: { status: 'error', error: 'Original technical failure', diagnostic: {
    code: 'source_unusable', stage: 'source_admission', recovery: 'Provide an original paper PDF',
  } }, online: true, llmAvailable: true, llm: {}, running: false, run: vi.fn(),
}) }));
afterEach(cleanup);

it('shows typed recovery and retains original technical error in a disclosure', () => {
  render(<DeepReviewSection itemKey="A" />);
  expect(screen.getByText('Provide an original paper PDF')).toBeTruthy();
  expect(screen.getByText('source_admission / source_unusable')).toBeTruthy();
  expect(screen.getByText('Original technical failure')).toBeTruthy();
});
