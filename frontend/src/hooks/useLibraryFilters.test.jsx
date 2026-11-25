// @vitest-environment jsdom
import { act, cleanup, renderHook } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { afterEach, expect, it } from 'vitest';
import useLibraryFilters from './useLibraryFilters.js';

afterEach(cleanup);

function setup(url) {
  const wrapper = ({ children }) => <MemoryRouter initialEntries={[url]}>{children}</MemoryRouter>;
  return renderHook(() => ({ f: useLibraryFilters(), search: useLocation().search }), { wrapper });
}

it('restores the search text, Show read, Rigor and the collection from the URL', () => {
  const { result } = setup('/library?text=agents&rd=1&q=high&c=K');
  const { f } = result.current;
  expect([f.search, f.searchInput, f.includeRead, f.clientFilters.quality, f.selectedCollection])
    .toEqual(['agents', 'agents', true, 'high', 'K']);
  expect(f.filtersActive).toBe(true);
  expect(result.current.search).toBe('?text=agents&rd=1&q=high&c=K');  // same keys: the URL is not rewritten
});

it('writes each change to the URL, keeping keys it does not own, and drops the retired w', () => {
  const { result } = setup('/library?w=x&open=ABC&b=could_read');
  expect(result.current.search).toBe('?open=ABC&b=could_read');
  act(() => { result.current.f.setSearchInput('graphs'); });
  expect(result.current.search).toBe('?open=ABC&b=could_read');  // typed, not applied
  act(() => { result.current.f.applySearch(); result.current.f.setIncludeRead(true); });
  expect(new URLSearchParams(result.current.search).get('text')).toBe('graphs');
  expect(new URLSearchParams(result.current.search).get('rd')).toBe('1');
  expect(new URLSearchParams(result.current.search).get('open')).toBe('ABC');
});

it('clearAll empties scope, search, Show read, smart filters and sort in one call, and leaves open alone', () => {
  const { result } = setup('/library?text=agents&rd=1&q=high&c=K&t=T&s=year&b=could_read&open=ABC');
  act(() => { result.current.f.clearAll(); });
  const { f } = result.current;
  expect([f.search, f.searchInput, f.includeRead, f.selectedCollection, f.selectedTag, f.filtersActive, f.sort.field])
    .toEqual(['', '', false, '', '', false, 'best']);
  expect(result.current.search).toBe('?open=ABC');
});

it('ignores values it does not know', () => {
  const { result } = setup('/library?q=zzz&rd=2&s=nope&b=nope&text=%20%20');
  const { f } = result.current;
  expect([f.clientFilters.quality, f.includeRead, f.sort.field, f.clientFilters.bands, f.search]).toEqual(['any', false, 'best', [], '']);
  expect(result.current.search).toBe('');
});
