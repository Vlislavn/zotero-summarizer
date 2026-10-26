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
  reviewAction.mockResolvedValue({ processed_id: 9, state: 'user_approved' });
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

it.each([
  ['must_read', /Must read/i, 'user_approved', 'approved'],
  ['should_read', /Should read/i, 'user_approved', 'approved'],
  ['could_read', /Could read/i, 'user_approved', 'approved'],
  ['dont_read', /Remove/i, 'user_rejected', 'rejected'],
])('acknowledges an exact item/state receipt for %s', async (priority, button, state, localState) => {
  fetchReview.mockResolvedValue({ items: [{ id: 9, title: 'Receipt control', review_ready: true }] });
  reviewAction.mockResolvedValue({ processed_id: 9, state });
  render(<MemoryRouter><Review /></MemoryRouter>);
  await screen.findByText('Receipt control');

  fireEvent.click(screen.getByRole('button', { name: button }));

  expect(await screen.findByText(`→ ${localState}`)).toBeTruthy();
  expect(reviewAction).toHaveBeenCalledWith(9, 'relabel', priority);
  expect(reviewAction).toHaveBeenCalledTimes(1);
  expect(screen.getByText(/Item 9: relabel .* OK/)).toBeTruthy();
  expect(screen.getByRole('button', { name: button }).disabled).toBe(true);
});

it.each([
  ['wrong ID', /Must read/i, { processed_id: 9001, state: 'user_approved' }],
  ['string ID', /Must read/i, { processed_id: '9', state: 'user_approved' }],
  ['missing ID', /Must read/i, { state: 'user_approved' }],
  ['missing state', /Must read/i, { processed_id: 9 }],
  ['opposite positive state', /Must read/i, { processed_id: 9, state: 'user_rejected' }],
  ['opposite rejection state', /Remove/i, { processed_id: 9, state: 'user_approved' }],
  ['unknown state', /Must read/i, { processed_id: 9, state: 'awaiting_review' }],
  ['empty receipt', /Must read/i, {}],
  ['null receipt', /Must read/i, null],
  ['array receipt', /Must read/i, []],
])('keeps a %s receipt unconfirmed without replaying the verdict', async (_case, button, receipt) => {
  fetchReview.mockResolvedValue({ items: [{ id: 9, title: 'Unconfirmed decision', review_ready: true }] });
  reviewAction.mockResolvedValue(receipt);
  render(<MemoryRouter><Review /></MemoryRouter>);
  await screen.findByText('Unconfirmed decision');

  fireEvent.click(screen.getByRole('button', { name: button }));

  expect(await screen.findByRole('alert')).toHaveProperty('textContent', expect.stringContaining('unconfirmed'));
  expect(screen.getByRole('alert').textContent).toContain('Refresh the queue before another action.');
  expect(screen.queryByText(/Item 9: .* OK/)).toBeNull();
  expect(screen.queryByText(/^→ /)).toBeNull();
  expect(screen.getByRole('button', { name: button }).disabled).toBe(false);
  expect(screen.getByRole('button', { name: 'Apply approved → Zotero' })).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
  await waitFor(() => expect(fetchReview).toHaveBeenCalledTimes(2));
  expect(reviewAction).toHaveBeenCalledTimes(1);
});

it.each([
  new Error('Connection lost'), new SyntaxError('Invalid JSON'),
  Object.assign(new Error('Server unavailable'), { status: 503 }),
])(
  'keeps a rejected request unconfirmed and does not automatically repeat it: %s', async (error) => {
    fetchReview.mockResolvedValue({ items: [{ id: 9, title: 'Unknown write outcome', review_ready: true }] });
    reviewAction.mockRejectedValue(error);
    render(<MemoryRouter><Review /></MemoryRouter>);
    await screen.findByText('Unknown write outcome');

    fireEvent.click(screen.getByRole('button', { name: /Must read/i }));

    expect(await screen.findByRole('alert')).toHaveProperty('textContent', expect.stringContaining('unconfirmed'));
    expect(screen.queryByText(/ OK$/)).toBeNull();
    expect(screen.queryByText(/^→ /)).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
    await waitFor(() => expect(fetchReview).toHaveBeenCalledTimes(2));
    expect(reviewAction).toHaveBeenCalledTimes(1);
  },
);

it.each([404, 422])('preserves a definite HTTP %s refusal without acknowledging or replaying it', async (status) => {
  fetchReview.mockResolvedValue({ items: [{ id: 9, title: 'Refused decision', review_ready: true }] });
  reviewAction.mockRejectedValue(Object.assign(new Error('Request refused'), { status }));
  render(<MemoryRouter><Review /></MemoryRouter>);
  await screen.findByText('Refused decision');

  fireEvent.click(screen.getByRole('button', { name: /Must read/i }));

  expect(await screen.findByRole('alert')).toHaveProperty('textContent', 'Item 9: relabel failed — Request refused');
  expect(screen.queryByText(/ OK$/)).toBeNull();
  expect(screen.queryByText(/^→ /)).toBeNull();
  expect(reviewAction).toHaveBeenCalledTimes(1);
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
