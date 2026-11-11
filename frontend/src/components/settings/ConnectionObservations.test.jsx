// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import ConnectionTestResult from './ConnectionTestResult.jsx';
import ConnectionImpact from './ConnectionImpact.jsx';

afterEach(cleanup);

it.each([401, 404])('shows the safe observed HTTP %s and direct connection repair', status => {
  const repair = vi.fn();
  render(<ConnectionTestResult onRepair={repair} result={{ status: 'fail', attempted: true,
    reason_code: 'inference_http_error', http_status: status,
    detail: `Server returned HTTP ${status}. Review the exact API destination.`, ownership: 'unresolved' }} />);
  expect(screen.getByText(`Server returned HTTP ${status}. Review the exact API destination.`)).toBeTruthy();
  expect(screen.getByText(/may still be running/)).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Review provider and model' }));
  expect(repair).toHaveBeenCalledTimes(1);
});

it('does not present a late successful response as current operational success', () => {
  render(<ConnectionTestResult result={{ status: 'operational', current: false, response: 'Old response', attempted: true }} />);
  expect(screen.getByText(/Test belongs to an older saved selection/)).toBeTruthy();
  expect(screen.queryByText(/Works — answered/)).toBeNull();
  expect(screen.queryByText('Old response')).toBeNull();
});

it('retains an independent Telegram admission explanation in impact', () => {
  render(<ConnectionImpact impact={{ stages: [], telegram: { selection: { model: 'local' }, admitted: false,
    detail: 'The selected Telegram provider is unavailable; its selection is retained unchanged.' } }} />);
  expect(screen.getByText(/Needs attention:/).textContent).toContain('The selected Telegram provider is unavailable; its selection is retained unchanged.');
});
