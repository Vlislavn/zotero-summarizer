// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import Search from './Search.jsx';

afterEach(() => {
  cleanup();
  try { sessionStorage.clear(); } catch { /* storage may be blocked by the test */ }
  vi.unstubAllGlobals();
});

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

it('keeps Search usable when session storage throws a SecurityError', async () => {
  vi.stubGlobal('sessionStorage', {
    getItem: () => { throw new DOMException('Blocked', 'SecurityError'); },
    setItem: () => { throw new DOMException('Blocked', 'SecurityError'); },
    removeItem: () => { throw new DOMException('Blocked', 'SecurityError'); },
    clear: () => { throw new DOMException('Blocked', 'SecurityError'); },
  });
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ items: [] }))));

  render(<Search />);

  expect(await screen.findByRole('heading', { name: 'Targeted Search' })).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Search' }).disabled).toBe(true);
});

it('sends only explicitly confirmed constraints, not restrictions inferred from the topic', async () => {
  const requests = [];
  vi.stubGlobal('fetch', vi.fn(async (path, options = {}) => {
    if (path === '/api/search/screen') {
      requests.push(JSON.parse(options.body));
      return new Response(JSON.stringify({ id: 'confirmed', status: 'reviewed', candidates: [], plan: { display: [
        { source: 'confirmation required: study_types', query: 'review' },
        { source: 'retrieval europepmc', query: 'observations: 0; status: unknown' },
      ] } }));
    }
    return new Response(JSON.stringify({ items: [] }));
  }));
  render(<Search />);
  fireEvent.change(screen.getByPlaceholderText(/e.g. LLM agents/), { target: { value: 'Only reviews about devices' } });
  fireEvent.click(screen.getByRole('button', { name: 'Search' }));
  await waitFor(() => expect(requests).toHaveLength(1));
  expect(requests[0]).toEqual({ query: 'Only reviews about devices', questions: [] });
  fireEvent.change(screen.getByLabelText('Only publication types'), { target: { value: ' Review\n\n' } });
  fireEvent.change(screen.getByLabelText('Excluded exact phrases'), { target: { value: 'animal' } });
  fireEvent.click(screen.getByRole('button', { name: 'Search' }));
  await waitFor(() => expect(requests).toHaveLength(2));
  expect(requests[1].constraints).toEqual({ must_include: [], must_not_include: ['animal'], study_types: ['Review'] });
  fireEvent.click(screen.getByText('Query plan (per source)'));
  expect(screen.getByText('observations: 0; status: unknown')).toBeTruthy();
  expect(screen.getByText('confirmation required: study_types')).toBeTruthy();
});
