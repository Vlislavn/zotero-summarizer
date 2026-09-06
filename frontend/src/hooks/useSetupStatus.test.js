import { describe, expect, it } from 'vitest';
import { deriveConfigured, derivePillars } from './useSetupStatus.js';

describe('setup status derivation', () => {
  it('uses backend readiness while keeping Zotero advisory', () => {
    const status = {
      ready: false,
      config: { valid: true, research_goals_count: 1 },
      llm: { api_key_present: true, reachable: false },
      zotero: { db_found: false },
      classifier: { trained: false },
    };

    expect(deriveConfigured({ ...status, configured: true })).toBe(true);
    expect(deriveConfigured(status)).toBe(false);
    expect(derivePillars(status).zotero).toBe(false);
    expect(deriveConfigured({ ...status, ready: true })).toBe(true);
  });

  it('distinguishes intentionally disabled AI from a broken provider', () => {
    const pillars = derivePillars({ llm: { enabled: false } });
    expect(pillars.llm).toBe(false);
    expect(pillars.llmDisabled).toBe(true);
  });
});
