// The backend owns the usable/not-usable gate; pillars explain it.

import { useQuery } from '@tanstack/react-query';
import { fetchSetupStatus } from '../api/setupApi.js';

export function deriveConfigured(status) {
  return Boolean(status?.ready);
}

export function deriveSubsystemIssues(status) {
  return (status?.subsystems || []).filter((s) => s && s.ready === false);
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
    subsystemIssues: deriveSubsystemIssues(status),
  };
}
