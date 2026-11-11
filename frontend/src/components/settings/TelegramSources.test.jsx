// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import { QueryClient } from '@tanstack/react-query';
import { renderRouter } from '../../testRouter.jsx';
import { afterEach, expect, it, vi } from 'vitest';
import TelegramSources from './TelegramSources.jsx';
import { telegramFeedApi } from '../../api/researchFeedApi.js';
vi.mock('../../api/researchFeedApi.js', () => ({ telegramFeedApi: {
  sources: vi.fn(), save: vi.fn(), status: vi.fn(), run: vi.fn(), collectConsent: vi.fn(),
} }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });
const saved = () => ({ sources: [{ kind: 'broadcast', username: 'alpha', message_id: null }],
  model: { provider: 'local', model: 'synthetic' }, local_providers: ['local'], llm_enabled: true,
  limits: { sources: 3, posts_per_channel: 3, replies_per_thread: 30, messages: 120,
    prompt_characters: 24000, context_tokens: 8192, output_tokens: 2048 } });
function mount(value = saved()) {
  telegramFeedApi.sources.mockResolvedValue(value);
  telegramFeedApi.status.mockResolvedValue({ state: 'idle', connection: 'unknown' });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return renderRouter(<TelegramSources />, client, ['/weekly?run=retained&task=telegram#sources']);
}
it('reads without activation, explicitly saves narrow selections, then requires consent for a bounded run', async () => {
  telegramFeedApi.save.mockImplementation(async (value) => ({ ...saved(), ...value }));
  mount();
  await screen.findByDisplayValue('alpha');
  expect(telegramFeedApi.run).not.toHaveBeenCalled();
  expect(screen.getByText(/Not verified/)).toBeTruthy();
  fireEvent.change(screen.getByLabelText('Telegram source 1'), { target: { value: 'https://t.me/beta' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save Telegram sources' }));
  await waitFor(() => expect(telegramFeedApi.save).toHaveBeenCalled());
  expect(telegramFeedApi.save.mock.calls[0][0].sources[0].username).toBe('https://t.me/beta');
  expect(telegramFeedApi.run).not.toHaveBeenCalled();
  expect(screen.queryByRole('button', { name: /Collect|Проверить каналы/ })).toBeNull();
  telegramFeedApi.collectConsent.mockResolvedValue({ consented: true });
  fireEvent.click(screen.getByLabelText(/Allow reading these channels/));
  await waitFor(() => expect(telegramFeedApi.collectConsent).toHaveBeenCalled());
  expect(telegramFeedApi.collectConsent.mock.calls[0][0]).toBe(true);
  expect(telegramFeedApi.run).not.toHaveBeenCalled();
  expect(screen.queryByRole('link', { name: /Open saved Telegram report/ })).toBeNull();
});
it('missing local providers has a canonical repair link retaining this Telegram task and Weekly run', async () => {
  const { router } = mount({ ...saved(), model: null, local_providers: [] });
  const link = await screen.findByRole('link', { name: 'Manage local AI connections' });
  expect(link.getAttribute('href')).toBe('/settings?task=connection');
  expect(screen.getByText(/No saved local provider/)).toBeTruthy();
  fireEvent.click(link);
  await waitFor(() => expect(router.state.location.pathname).toBe('/settings'));
  expect(router.state.location.state.returnTo).toBe('/weekly?run=retained&task=telegram#sources');
  expect(telegramFeedApi.save).not.toHaveBeenCalled();
  expect(telegramFeedApi.run).not.toHaveBeenCalled();
});
it('uses bounded integer controls and cannot re-consent a changed unsaved selection', async () => {
  mount(); await screen.findByDisplayValue('alpha'); fireEvent.click(screen.getByText('Bounded work limits'));
  const controls = screen.getAllByRole('spinbutton'); expect(controls).toHaveLength(6);
  expect(controls.map(input => input.step)).toEqual(['1', '1', '1', '1', '1', '1']);
  expect(controls[0].min).toBe('1'); expect(controls[0].max).toBe('3');
  fireEvent.change(screen.getByLabelText('Telegram source 1'), { target: { value: 'changed-synthetic' } });
  expect(screen.getByText(/Save your channel changes/)).toBeTruthy();
  expect(telegramFeedApi.run).not.toHaveBeenCalled();
});
