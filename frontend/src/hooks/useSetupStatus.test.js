import { describe, expect, it } from 'vitest';
import { deriveConfigured, derivePillars } from './useSetupStatus.js';

describe('setup status derivation', () => {
  it('treats Zotero as advisory for configured state', () => {
    const status = {
      config: { valid: true, research_goals_count: 1 },
      llm: { api_key_present: true, reachable: false },
      zotero: { db_found: false },
      classifier: { trained: false },
    };

    expect(deriveConfigured(status)).toBe(true);
    expect(derivePillars(status).zotero).toBe(false);
  });
});
