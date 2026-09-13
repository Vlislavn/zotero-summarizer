// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import Search from './Search.jsx';

afterEach(() => { cleanup(); sessionStorage.clear(); vi.unstubAllGlobals(); });

it('shows every server query variant and files identical-title cards by their distinct IDs', async () => {
  const writes = [];
  const research = {
    id: 'search-test', status: 'reviewed',
    plan: {
      arxiv: 'broad topic', arxiv_variants: ['"precise topic"', 'broad topic'], openreview: 'peer-reviewed topic',
      display: [
        { source: 'arxiv', query: '"precise topic"' },
        { source: 'arxiv', query: 'broad topic' },
        { source: 'openreview', query: 'peer-reviewed topic' },
      ],
    },
    candidates: [{ candidate_id: 'local:first', title: 'Same title' }, { candidate_id: 'local:second', title: 'Same title' }],
  };
  sessionStorage.setItem('zs.searchSession', JSON.stringify({ id: research.id }));
  vi.stubGlobal('fetch', vi.fn(async (path, options = {}) => {
    let body;
    if (path === '/api/search/search-test') body = research;
    else if (path === '/api/zotero/collections') body = { items: [] };
    else if (path === '/api/search/search-test/materialize') {
      writes.push(JSON.parse(options.body));
      body = { status: 'added', zotero_key: `Z${writes.length}` };
    } else throw new Error(`Unexpected request: ${path}`);
    return new Response(JSON.stringify(body));
  }));
  render(<Search />);
  await screen.findByText('Query plan (per source)');
  fireEvent.click(screen.getByText('Query plan (per source)'));
  expect(screen.getByText('"precise topic"')).toBeTruthy();
  expect(screen.getByText('broad topic')).toBeTruthy();
  expect(screen.getByText('peer-reviewed topic')).toBeTruthy();

  fireEvent.click(screen.getAllByRole('button', { name: 'Add to library' })[0]);
  await waitFor(() => expect(screen.getAllByRole('button', { name: 'Add to library' })).toHaveLength(1));
  fireEvent.click(screen.getByRole('button', { name: 'Add to library' }));
  await waitFor(() => expect(screen.getAllByText('✓ In library')).toHaveLength(2));
  expect(writes).toEqual([
    { candidate_id: 'local:first', collection_key: null },
    { candidate_id: 'local:second', collection_key: null },
  ]);
});

it('sorts the complete result pool and remembers relevance + prestige on remount', async () => {
  const research = { id: 'sorted', status: 'reviewed', candidates: [
    { candidate_id: 'a', title: 'Most relevant', url: 'https://example.com/a', query_score: 0.9, cited_by_count: 0 },
    { candidate_id: 'b', title: 'Well cited', url: 'https://example.com/b', query_score: 0.85, cited_by_count: 100, relevance_band: 'weak' },
  ] };
  sessionStorage.setItem('zs.searchSession', JSON.stringify({ id: research.id }));
  vi.stubGlobal('fetch', vi.fn(async (path) => new Response(JSON.stringify(
    path === '/api/search/sorted' ? research : { items: [] },
  ))));
  const page = render(<Search />);
  const selector = await screen.findByRole('combobox', { name: 'Sort by' });
  fireEvent.change(selector, { target: { value: 'relevance_prestige' } });
  expect(screen.getAllByRole('link').map((link) => link.textContent)).toEqual(['Well cited', 'Most relevant']);
  expect(screen.queryByText(/weaker match/)).toBeNull();
  page.unmount();
  render(<Search />);
  expect((await screen.findByRole('combobox', { name: 'Sort by' })).value).toBe('relevance_prestige');
  expect(screen.getAllByRole('link').map((link) => link.textContent)).toEqual(['Well cited', 'Most relevant']);
});
