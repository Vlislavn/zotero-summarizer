// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import Review from './Review.jsx';
import { fetchReview, reviewAction, reviewConfirmAllGateRejected } from '../api/reviewApi.js';

vi.mock('../api/reviewApi.js', () => ({
  fetchReview: vi.fn(), reviewAction: vi.fn(), reviewApplyAll: vi.fn(), reviewConfirmAllGateRejected: vi.fn(),
}));
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.clearAllMocks(); });

it('confirms the visible IDs and removes acknowledged rows from the queue', async () => {
  fetchReview.mockResolvedValue({ items: [{ id: 9, title: 'Visible rejection', reading_priority: 'dont_read' }] });
  reviewConfirmAllGateRejected.mockResolvedValue({ confirmed: 1, skipped: 0 });
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  render(<MemoryRouter initialEntries={['/?state=gate_rejected']}><Review /></MemoryRouter>);
  await screen.findByText('Visible rejection');
  fireEvent.click(screen.getByRole('button', { name: /Confirm remaining/ }));
  await waitFor(() => expect(reviewConfirmAllGateRejected).toHaveBeenCalledWith([9]));
  expect(await screen.findByText(/Confirmed 1/)).toBeTruthy();
  expect(screen.queryByText('Visible rejection')).toBeNull();
  expect(screen.getByRole('button', { name: /Confirm remaining/ }).disabled).toBe(true);
});

it('does not send an individually relabelled row in a later bulk confirmation', async () => {
  fetchReview.mockResolvedValue({ items: [{ id: 9, title: 'Already reviewed', reading_priority: 'dont_read' }] });
  reviewAction.mockResolvedValue({ state: 'user_approved' });
  render(<MemoryRouter initialEntries={['/?state=gate_rejected']}><Review /></MemoryRouter>);
  await screen.findByText('Already reviewed');
  fireEvent.click(screen.getByRole('button', { name: /Must read/i }));
  await screen.findByText('→ approved');
  expect(screen.getByRole('button', { name: /Confirm remaining/ }).disabled).toBe(true);
  expect(reviewConfirmAllGateRejected).not.toHaveBeenCalled();
});

it('keeps rows available for retry when bulk confirmation fails', async () => {
  fetchReview.mockResolvedValue({ items: [{ id: 9, title: 'Retry me', reading_priority: 'dont_read' }] });
  reviewConfirmAllGateRejected.mockRejectedValue(new Error('Write failed'));
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  render(<MemoryRouter initialEntries={['/?state=gate_rejected']}><Review /></MemoryRouter>);
  await screen.findByText('Retry me');
  fireEvent.click(screen.getByRole('button', { name: /Confirm remaining/ }));
  expect(await screen.findByText(/Bulk-confirm failed/)).toBeTruthy();
  expect(screen.getByText('Retry me')).toBeTruthy();
  expect(screen.getByRole('button', { name: /Confirm remaining/ }).disabled).toBe(false);
});
