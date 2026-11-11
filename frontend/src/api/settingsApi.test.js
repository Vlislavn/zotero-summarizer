import { afterEach, expect, it, vi } from 'vitest';
import { fetchConfig, updateConfig } from './settingsApi.js';
const complete = { _revision: 'opaque-r1', llm_enabled: false, research_goals: ['Synthetic goal'], triage_criteria: ['Evidence'], output_language: 'English',
  llm_routing: { providers: [], default: {} }, retained: { future: true } };
afterEach(() => vi.unstubAllGlobals());
function respond(value) { vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => value })); }
it.each([null, {}, { ...complete, _revision: undefined }, { ...complete, _revision: 7 }, { ...complete, _revision: '' },
  { ...complete, llm_enabled: 'false' }, { ...complete, research_goals: 'not an array' }, { ...complete, triage_criteria: [{}] },
  { ...complete, llm_routing: null }, { ...complete, llm_routing: { providers: {} } }])(
  'rejects incomplete saved snapshots %j instead of manufacturing defaults/a revision', async value => {
    respond(value); await expect(fetchConfig()).rejects.toThrow('Saved configuration response is incomplete'); expect(fetch).toHaveBeenCalledTimes(1);
  });
it.each([{}, { status: 'accepted', config: complete }, { status: 'ok', config: {} }, { status: 'ok', config: { ...complete, _revision: null } }])(
  'does not treat partial configuration acknowledgement %j as a complete saved snapshot', async value => {
    respond(value); await expect(updateConfig(complete)).rejects.toThrow(); expect(fetch).toHaveBeenCalledTimes(1);
  });
it('retains the complete opaque-revision snapshot and all unowned fields unchanged', async () => {
  respond(complete); expect(await fetchConfig()).toBe(complete); expect(fetch).toHaveBeenCalledTimes(1);
});
