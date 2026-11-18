import { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  EMPTY_FILTERS, DEFAULT_SORT, isFilterActive, serializeFilters, hydrateFilters,
  serializeSort, hydrateSort,
} from '../utils/relevanceBands.js';

// Everything that narrows or orders the Library list, and its URL: the server scope (collection `c`,
// tag `t`, search text `text`, Show read `rd=1`) and the client filters and sort (`b pr g q code vn`, `s sd`).
// The state is the source;
// an effect mirrors it into the URL (`replace`, defaults omitted, other keys such as `open` kept).
// `w` (the retired Why filter) is no longer read: an old link loses it on open, and so does any unknown value.
const OWN_KEYS = ['c', 't', 'text', 'rd', 'b', 'pr', 'g', 'w', 'q', 'code', 'vn', 's', 'sd'];
const canon = (params) => [...params].map(([k, v]) => `${k}=${v}`).sort().join('&');

// `onScopeChange` runs when the collection or tag is picked (the page drops its row selection).
export default function useLibraryFilters({ onScopeChange } = {}) {
  const [params, setParams] = useSearchParams();
  const [clientFilters, setClientFilters] = useState(() => hydrateFilters(params));
  const [sort, setSort] = useState(() => hydrateSort(params));
  const [selectedCollection, setCollection] = useState(() => params.get('c') || '');
  const [selectedTag, setTag] = useState(() => params.get('t') || '');
  const [search, setSearch] = useState(() => (params.get('text') || '').trim());  // applied: what the server was asked
  const [searchInput, setSearchInput] = useState(search);                       // what is typed in the box
  const [includeRead, setIncludeRead] = useState(() => params.get('rd') === '1');

  const urlParams = useMemo(() => {
    const own = { ...serializeFilters(clientFilters), ...serializeSort(sort) };
    if (selectedCollection) own.c = selectedCollection;
    if (selectedTag) own.t = selectedTag;
    if (search) own.text = search;
    if (includeRead) own.rd = '1';
    return own;
  }, [clientFilters, sort, selectedCollection, selectedTag, search, includeRead]);

  // Only navigate when our keys actually differ: a bare setParams on every render can loop, since
  // its identity changes with the location it just updated.
  useEffect(() => {
    const current = new URLSearchParams([...params].filter(([k]) => OWN_KEYS.includes(k)));
    if (canon(current) === canon(new URLSearchParams(urlParams))) return;
    const merged = new URLSearchParams([...params].filter(([k]) => !OWN_KEYS.includes(k)));
    Object.entries(urlParams).forEach(([k, v]) => merged.set(k, v));
    setParams(merged, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [urlParams]);

  const selectCollection = useCallback((key) => { setCollection(key); onScopeChange?.(); }, [onScopeChange]);
  const selectTag = useCallback((tag) => { setTag(tag); onScopeChange?.(); }, [onScopeChange]);
  const applySearch = useCallback(() => setSearch(searchInput.trim()), [searchInput]);
  const clearSearch = useCallback(() => { setSearchInput(''); setSearch(''); }, []);
  const clearScope = useCallback(() => { setCollection(''); setTag(''); }, []);
  const clearFilters = useCallback(() => setClientFilters(EMPTY_FILTERS), []);
  // The page's single «Clear all»: scope, search, Show read, smart filters and the sort (not the Order preset).
  const clearAll = useCallback(() => {
    clearScope(); clearSearch(); clearFilters(); setIncludeRead(false); setSort(DEFAULT_SORT);
  }, [clearScope, clearSearch, clearFilters]);

  return {
    clientFilters, setClientFilters, filtersActive: Boolean(isFilterActive(clientFilters)), clearFilters,
    sort, setSort,
    selectedCollection, selectedTag, selectCollection, selectTag, clearScope,
    search, searchInput, setSearchInput, applySearch, clearSearch,
    includeRead, setIncludeRead,
    clearAll,
    // The own keys as they stand: changes whenever a filter or the sort does (resets the list's reveal).
    filterSig: JSON.stringify({ ...serializeFilters(clientFilters), ...serializeSort(sort) }),
  };
}
