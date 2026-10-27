// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';
import TelegramSources from './TelegramSources.jsx';
import { telegramFeedApi } from '../../api/researchFeedApi.js';
vi.mock('../../api/researchFeedApi.js', () => ({ telegramFeedApi: {
  sources: vi.fn(), save: vi.fn(), status: vi.fn(), run: vi.fn(),
} }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });
const saved = () => ({ sources: [{ kind: 'broadcast', username: 'alpha', message_id: null }],
  model: { provider: 'local', model: 'synthetic' }, local_providers: ['local'], llm_enabled: true,
  limits: { sources: 3, posts_per_channel: 3, replies_per_thread: 30, messages: 120,
    prompt_characters: 24000, context_tokens: 8192, output_tokens: 2048 } });
function mount() {
  telegramFeedApi.sources.mockResolvedValue(saved());
  telegramFeedApi.status.mockResolvedValue({ state: 'idle', connection: 'unknown' });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><TelegramSources /></QueryClientProvider>);
}
it('reads without activation, explicitly saves narrow selections, then requires consent for a bounded run', async () => {
  telegramFeedApi.save.mockImplementation(async (value) => ({ ...saved(), ...value }));
  telegramFeedApi.run.mockResolvedValue({ state: 'partial', connection: 'verified', verified_at: '2026-10-24', report_id: 'telegram-review-synthetic' });
  mount();
  await screen.findByDisplayValue('alpha');
  expect(telegramFeedApi.run).not.toHaveBeenCalled();
  expect(screen.getByText(/Not verified/)).toBeTruthy();
  fireEvent.change(screen.getByLabelText('Telegram source 1'), { target: { value: 'https://t.me/beta' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save Telegram sources' }));
  await waitFor(() => expect(telegramFeedApi.save).toHaveBeenCalled());
  expect(telegramFeedApi.save.mock.calls[0][0].sources[0].username).toBe('https://t.me/beta');
  expect(telegramFeedApi.run).not.toHaveBeenCalled();
  const run = screen.getByRole('button', { name: 'Collect & review selected sources' });
  expect(run.disabled).toBe(true);
  fireEvent.click(screen.getByLabelText(/Allow bounded read-only collection/));
  fireEvent.click(run);
  await screen.findByRole('link', { name: 'Open saved Telegram report in Weekly' });
  expect(telegramFeedApi.run.mock.calls[0][0]).toEqual({ consent: true });
  expect(screen.getByText(/Connected.*2026-10-24/)).toBeTruthy();
  expect(screen.getByText(/partial/)).toBeTruthy();
});
it('does not guess connection from saved configuration or conceal a failed run', async () => {
  telegramFeedApi.run.mockRejectedValue(new Error('Collection failed'));
  mount();
  await screen.findByDisplayValue('alpha');
  fireEvent.click(screen.getByLabelText(/Allow bounded read-only collection/));
  fireEvent.click(screen.getByRole('button', { name: 'Collect & review selected sources' }));
  await screen.findByText(/Collection failed/);
  expect(screen.queryByText(/^Connected/)).toBeNull();
});
