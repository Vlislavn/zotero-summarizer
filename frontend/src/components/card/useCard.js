import { useQuery } from '@tanstack/react-query';
import { fetchCard } from '../../api/cardApi.js';

// GET /api/card/{key}; one cache entry shared by the facts card, its legend and the
// AI TL;DR row.
export default function useCard(cardKey) {
  return useQuery({
    queryKey: ['card', cardKey],
    queryFn: () => fetchCard(cardKey),
    enabled: Boolean(cardKey),
    staleTime: 60_000,
  });
}
