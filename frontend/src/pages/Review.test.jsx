// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, useLocation, useSearchParams } from 'react-router-dom';
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
  fetchReview.mockResolvedValue({ items: [{ id: 9, title: 'Already reviewed', reading_priority: 'dont_read', review_ready: true }] });
  reviewAction.mockResolvedValue({ state: 'user_approved' });
  render(<MemoryRouter initialEntries={['/?state=gate_rejected']}><Review /></MemoryRouter>);
  await screen.findByText('Already reviewed');
  fireEvent.click(screen.getByRole('button', { name: /Must read/i }));
  await screen.findByText('→ approved');
  expect(screen.getByRole('button', { name: /Confirm remaining/ }).disabled).toBe(true);
  expect(reviewConfirmAllGateRejected).not.toHaveBeenCalled();
});

it('requires a review for positive relabels while keeping rejection available', async () => {
  fetchReview.mockResolvedValue({ items: [{ id: 9, title: 'Awaiting full review',
    stable_feed_key: 'feed:g:abcdef', reading_priority: 'could_read', review_ready: false }] });
  render(<MemoryRouter><Review /></MemoryRouter>);
  await screen.findByText('Awaiting full review');
  expect(screen.getByRole('button', { name: /Must read/i }).disabled).toBe(true);
  expect(screen.getByRole('button', { name: /Remove/i }).disabled).toBe(false);
  expect(screen.getByText('Generate a review before adding').getAttribute('href')).toContain('/paper/feed%3Ag%3Aabcdef');
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

function CurrentSearch() {
  const location = useLocation();
  return <output data-testid="current-search">{location.search}</output>;
}

function NavigateToGateRejected() {
  const [, setSearchParams] = useSearchParams();
  return <button type="button" onClick={() => setSearchParams({ state: 'gate_rejected' })}>Navigate to rejected pile</button>;
}

it('loads a deep-linked pile and keeps URL state synchronized with pile changes', async () => {
  fetchReview.mockResolvedValue({ items: [] });
  render(
    <MemoryRouter initialEntries={['/review?state=gate_rejected&from=legacy']}>
      <CurrentSearch />
      <NavigateToGateRejected />
      <Review />
    </MemoryRouter>,
  );

  await waitFor(() => expect(fetchReview).toHaveBeenCalledWith({
    state: 'gate_rejected', limit: 500, sort: 'border',
  }));
  fireEvent.click(screen.getByRole('button', { name: 'Awaiting review' }));
  await waitFor(() => expect(fetchReview).toHaveBeenLastCalledWith({
    state: 'awaiting_review', limit: 500, sort: 'border',
  }));
  expect(screen.getByTestId('current-search').textContent).toContain('state=awaiting_review');
  expect(screen.getByTestId('current-search').textContent).toContain('from=legacy');

  fireEvent.click(screen.getByRole('button', { name: 'Navigate to rejected pile' }));
  await waitFor(() => expect(fetchReview).toHaveBeenLastCalledWith({
    state: 'gate_rejected', limit: 500, sort: 'border',
  }));
});
