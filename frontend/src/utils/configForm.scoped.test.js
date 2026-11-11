import { expect, it } from 'vitest';
import { configToFormState, scopedFormStateToConfig } from './configForm.js';

const saved = { _revision: 'original-revision', llm_enabled: true, output_language: '', research_goals: ['Saved'],
  triage_criteria: ['Evidence'], university_access: { enabled: false, login_url: '', channel: 'retained', timeout: 42 },
  llm_routing: { providers: [{ name: 'owned', type: 'openai', base_url: 'http://127.0.0.1:9999/v1', auth_mode: 'none', temperature: 0.7 }],
    default: { provider: 'owned', model: 'saved' }, feed: { model: 'exception' } },
  prestige: { extra: ['unchanged'] }, llm: { legacy: 'unchanged' } };

it('selection writes only its two fields, preserving exact unowned values and captured revision', () => {
  const form = { ...configToFormState(saved), research_goals_text: 'New goal\nSecond goal', triage_criteria_text: 'New criterion',
    output_language: 'French', llm_enabled: false, ua_login_url: 'https://draft.invalid', llm_routing: {} };
  const before = JSON.stringify(saved);
  expect(scopedFormStateToConfig(form, saved, ['research_goals', 'triage_criteria'])).toEqual({ ...saved,
    research_goals: ['New goal', 'Second goal'], triage_criteria: ['New criterion'] });
  expect(JSON.stringify(saved)).toBe(before);
});
it('AI mode does not publish unrelated language or connection edits', () => {
  const form = { ...configToFormState(saved), llm_enabled: false, output_language: 'French', llm_routing: {} };
  expect(scopedFormStateToConfig(form, saved, ['llm_enabled'])).toEqual({ ...saved, llm_enabled: false });
});
it('language does not normalize or reset any other branch', () => {
  const form = { ...configToFormState(saved), output_language: 'Русский', research_goals_text: 'Other draft' };
  expect(scopedFormStateToConfig(form, saved, ['output_language'])).toEqual({ ...saved, output_language: 'Русский' });
});
it('access preserves all unsurfaced nested values and unrelated routing/options', () => {
  const form = { ...configToFormState(saved), ua_enabled: true, ua_login_url: 'https://library.invalid/login' };
  expect(scopedFormStateToConfig(form, saved, ['university_access'])).toEqual({ ...saved,
    university_access: { ...saved.university_access, enabled: true, login_url: 'https://library.invalid/login', ezproxy_prefix: '', cookie_browser: '' } });
});
