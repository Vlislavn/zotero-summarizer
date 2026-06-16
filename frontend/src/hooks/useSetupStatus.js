// Single seam for first-run readiness. Everything that needs to know "is the
// app configured?" / "which pillars are green?" reads through this hook so the
// derivation lives in exactly one place (frozen-contract math, not scattered).
//
// Derived (per the frozen contract):
//   isConfigured = config.valid && config.research_goals_count>0
//                  && llm.api_key_present && zotero.db_found
//   pillars = { zotero, llm, goals, model } booleans for the readiness strip.

import { useQuery } from '@tanstack/react-query';
import { fetchSetupStatus } from '../api/setupApi.js';

export function deriveConfigured(status) {
  if (!status) return false;
  const { config, llm, zotero } = status;
  return Boolean(
    config?.valid &&
      (config?.research_goals_count || 0) > 0 &&
      llm?.api_key_present &&
      zotero?.db_found,
  );
}

export function derivePillars(status) {
  const config = status?.config || {};
  const llm = status?.llm || {};
  const zotero = status?.zotero || {};
  const classifier = status?.classifier || {};
  return {
    zotero: Boolean(zotero.db_found),
    llm: Boolean(llm.api_key_present && llm.reachable),
    goals: Boolean(config.valid && (config.research_goals_count || 0) > 0),
    model: Boolean(classifier.trained),
  };
}

export function useSetupStatus(options = {}) {
  const query = useQuery({
    queryKey: ['setup-status'],
    queryFn: fetchSetupStatus,
    staleTime: 30_000,
    ...options,
  });
  const status = query.data || null;
  return {
    ...query,
    status,
    isConfigured: deriveConfigured(status),
    pillars: derivePillars(status),
  };
}
