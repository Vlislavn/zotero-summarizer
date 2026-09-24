// Citation count is the prestige proxy available in saved Search sessions.
// ponytail: raw counts favor older fields/papers; use field/year percentiles when supplied.
export function sortSearchCandidates(candidates, mode) {
  if (mode === 'recommended') return candidates;
  const known = candidates.map((c) => c.cited_by_count).filter(Number.isFinite).sort((a, b) => a - b);
  const middle = Math.floor(known.length / 2);
  const median = known.length ? (known[middle] + known[Math.floor((known.length - 1) / 2)]) / 2 : 0;
  const ceiling = Math.log1p(Math.max(0, ...known));
  const score = (candidate) => {
    if (!Number.isFinite(candidate.query_score)) return -Infinity;
    if (mode !== 'relevance_prestige' || !ceiling) return candidate.query_score;
    const citations = Number.isFinite(candidate.cited_by_count) ? candidate.cited_by_count : median;
    return 0.85 * candidate.query_score + 0.15 * Math.log1p(Math.max(0, citations)) / ceiling;
  };
  return [...candidates].sort((a, b) => Number(Boolean(a.is_retracted)) - Number(Boolean(b.is_retracted)) || score(b) - score(a));
}
