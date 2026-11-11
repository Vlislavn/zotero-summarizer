// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, screen } from '@testing-library/react';
import { QueryClient } from '@tanstack/react-query';
import { renderRouter } from '../../testRouter.jsx';
import { RssSourcesTask, TelegramSourcesTask } from './SourceTasks.jsx';

vi.mock('./RssFeedsSection.jsx', () => ({ default: () => <p>RSS controls</p> }));
vi.mock('./TelegramSources.jsx', () => ({ default: () => <p>Telegram controls</p> }));

afterEach(cleanup);

it.each([
  ['cold RSS', RssSourcesTask, '/today', '?task=rss', 'Back to Today', '/today'],
  ['cold Telegram', TelegramSourcesTask, '/weekly', '?run=saved&task=telegram', 'Back to Weekly', '/weekly?run=saved'],
  ['Setup RSS', RssSourcesTask, '/today', '?task=rss', 'Back to Setup', '/setup'],
  ['Setup Telegram', TelegramSourcesTask, '/weekly', '?task=telegram', 'Back to Setup', '/setup'],
])('%s keeps its caller or workspace return destination', (_case, Task, pathname, search, label, href) => {
  const client = new QueryClient();
  const state = label === 'Back to Setup' ? { returnTo: '/setup' } : null;
  const view = renderRouter(<Task />, client, [{ pathname, search, state }]);
  try {
    expect(screen.getByRole('link', { name: label }).getAttribute('href')).toBe(href);
  } finally {
    view.unmount();
    client.clear();
  }
});
