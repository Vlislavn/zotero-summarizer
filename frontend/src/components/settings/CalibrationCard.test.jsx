// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
const api = vi.hoisted(() => ({ calibrateSetup: vi.fn(), fetchCalibrateStatus: vi.fn() }));
vi.mock('../../api/settingsApi.js', () => api);
import CalibrationCard from './CalibrationCard.jsx';
let client;
let finish;
function mount() { return render(<QueryClientProvider client={client}><CalibrationCard /></QueryClientProvider>); }
async function start() {
  const button = screen.getByRole('button', { name: 'Start review calibration' });
  await waitFor(() => expect(button.disabled).toBe(false)); fireEvent.click(button);
  await waitFor(() => expect(api.calibrateSetup).toHaveBeenCalledTimes(1));
}
beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  api.fetchCalibrateStatus.mockResolvedValue({ status: 'idle' });
  api.calibrateSetup.mockImplementation(() => new Promise(resolve => { finish = () => resolve({ status: 'running', total: 6, completed: 0 }); }));
});
afterEach(async () => { cleanup(); if (finish) await act(async () => finish()); finish = null; client.clear(); vi.resetAllMocks(); });
it('a status read cannot reset the observer and admit a second start while the first request is pending', async () => {
  mount(); await start(); expect(screen.getByRole('button', { name: 'Read calibration status' }).disabled).toBe(true);
  expect(api.calibrateSetup.mock.calls[0][0]).toEqual({ papers: 3 });
});
it('remount retains the pending start owner even if a fresh status read still reports idle', async () => {
  const first = mount(); await start(); first.unmount(); mount();
  await waitFor(() => expect(api.fetchCalibrateStatus).toHaveBeenCalledTimes(2));
  const read = screen.getByRole('button', { name: 'Read calibration status' }); expect(read.disabled).toBe(true);
  expect(screen.queryByRole('button', { name: 'Start review calibration' })).toBeNull(); expect(api.calibrateSetup).toHaveBeenCalledTimes(1);
});
it.each([{}, { status: 'unknown' }])('an unobserved status %j blocks starting, rather than falling back to idle', async status => {
  api.fetchCalibrateStatus.mockResolvedValue(status); mount(); await screen.findByText(/Status unavailable\./);
  expect(screen.getByRole('button', { name: 'Start review calibration' }).disabled).toBe(true); expect(api.calibrateSetup).not.toHaveBeenCalled();
});
it('lost start acknowledgement never auto-restarts and needs a separate successful status read', async () => {
  api.calibrateSetup.mockRejectedValue(new TypeError('Synthetic lost acknowledgement')); mount(); await start();
  await screen.findByText(/Calibration request failed:/); await waitFor(() => expect(api.fetchCalibrateStatus).toHaveBeenCalledTimes(2));
  const button = screen.getByRole('button', { name: 'Start review calibration' }); expect(button.disabled).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: 'Read calibration status' })); await waitFor(() => expect(button.disabled).toBe(false));
  expect(api.calibrateSetup).toHaveBeenCalledTimes(1);
});
