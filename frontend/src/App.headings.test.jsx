// @vitest-environment jsdom
import { cleanup, screen } from '@testing-library/react';
import { QueryClient } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';
import { renderRouter } from './testRouter.jsx';
import App from './App.jsx';

// Every routed page owns exactly one <h1> (NavBar's app name is not a heading), also while
// its data is loading or has failed (all requests rejected here).
const ROUTES = ['/today', '/settings', '/library', '/library?mode=batch', '/search', '/weekly', '/weekly?past=list',
  '/conferences', '/paper/ABC123', '/ops', '/ops?tab=triage', '/ops?tab=system', '/ops?tab=system&task=readiness', '/setup'];

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it.each(ROUTES)('%s renders exactly one h1', async (route) => {
  vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new TypeError('offline'))));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  renderRouter(<App />, client, [route]);
  await new Promise((resolve) => setTimeout(resolve, 50));
  expect(screen.getAllByRole('heading', { level: 1 }).map((h) => h.textContent)).toHaveLength(1);
});
