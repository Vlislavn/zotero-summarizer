import { OrderCaption } from './OrderControls.jsx';

// Backend cap of hybrid meaning search (services/library/_search.py _TOP_N).
const SEARCH_TOP_N = 50;

// The backend caps meaning search at 50 rows; say so, and that filters cut within them.
// While the reranker downloads the order is the first-pass fusion: said here, next to the results.
function SearchCaption({ rawCount, searchQuery, hasActiveFilters, shownCount, rerankerLoading }) {
  return (
    <span data-testid="search-caption">
      {rawCount >= SEARCH_TOP_N
        ? `· Top ${SEARCH_TOP_N} by meaning for “${searchQuery}”${hasActiveFilters ? ` · ${shownCount} of ${SEARCH_TOP_N} match filters` : ''}`
        : `· ranked by similarity to “${searchQuery}”`}
      {rerankerLoading && ' · quick mode while the ranking model downloads (first search only)'}
    </span>
  );
}

// The one-line note beside the Order control: which order the list is in (Best match only).
export default function RankNote({
  sort, offline, semantic, searchQuery, semanticUnavailable, modelReady, orderOn, phone, order, orderMeta,
  rawCount, hasActiveFilters, shownCount, rerankerLoading,
}) {
  if (sort.field !== 'best') return null;
  if (offline) return <span>· cached working set order</span>;
  if (semantic) {
    return (
      <SearchCaption
        rawCount={rawCount}
        searchQuery={searchQuery}
        hasActiveFilters={hasActiveFilters}
        shownCount={shownCount}
        rerankerLoading={rerankerLoading}
      />
    );
  }
  if (searchQuery.trim() && semanticUnavailable) {
    return <span data-testid="search-caption">· exact text matches for “{searchQuery}” — meaning search is off</span>;
  }
  if (!modelReady) return <span>· ranked by recency (model not ready yet)</span>;
  if (orderOn) return phone ? null : <OrderCaption order={order} meta={orderMeta} />;
  return (
    <span title="Best first: your own Must read papers, then model relevance + goal match + author/venue and quality evidence. Review suggestions do not affect this order.">
      · relevance + goals + evidence
    </span>
  );
}
