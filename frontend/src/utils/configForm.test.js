import { describe, expect, it } from 'vitest';
import { configToFormState, formStateToConfig } from './configForm.js';

describe('configForm transforms', () => {
  const baseConfig = {
    research_goals: ['goal a', 'goal b'],
    triage_criteria: ['crit a'],
    output_language: 'English',
    // Legacy llm block — must be round-tripped untouched, never UI-edited.
    llm: { draft_model: 'm1', refine_model: 'm2', api_base: 'http://x/v1', api_key_env: 'K' },
    corpus: { similarity_threshold: -0.3 },
    classifier_gate: { enabled: false, model_name: 'tabpfn', drop_priorities: ['dont_read'] },
    llm_routing: {
      providers: [{ name: 'p', type: 'openai', base_url: 'http://x/v1', api_key_env: 'K' }],
      default: { provider: 'p', model: 'm' },
      feed: { provider: null, model: null },
      backlog: { provider: null, model: null },
      deep_review: { provider: null, model: null },
    },
    // An unknown nested branch the form never surfaces — must survive a round-trip.
    prestige: { weight: 0.15 },
  };

  it('configToFormState drops the legacy llm_* keys', () => {
    const form = configToFormState(baseConfig);
    expect(form).not.toHaveProperty('llm_draft_model');
    expect(form).not.toHaveProperty('llm_refine_model');
    expect(form).not.toHaveProperty('llm_api_base');
    expect(form).not.toHaveProperty('llm_api_key_env');
    expect(form.research_goals_text).toBe('goal a\ngoal b');
  });

  it('formStateToConfig does NOT write a next.llm block (preserves baseConfig llm)', () => {
    const form = configToFormState(baseConfig);
    const out = formStateToConfig(form, baseConfig);
    // The legacy block is round-tripped from baseConfig, byte-identical.
    expect(out.llm).toEqual(baseConfig.llm);
  });

  it('round-trips an unchanged form to an equivalent config', () => {
    const form = configToFormState(baseConfig);
    const out = formStateToConfig(form, baseConfig);
    expect(out.research_goals).toEqual(baseConfig.research_goals);
    expect(out.triage_criteria).toEqual(baseConfig.triage_criteria);
    expect(out.output_language).toBe('English');
    expect(out.llm_routing).toEqual(baseConfig.llm_routing);
    // Unknown branch survives.
    expect(out.prestige).toEqual({ weight: 0.15 });
  });
});
