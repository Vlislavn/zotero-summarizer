import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useLocation } from 'react-router-dom';
import { fetchConfig, updateConfig } from '../../api/settingsApi.js';
import { PROCESSING_KNOBS, cleanGoal, configToFormState, processingKnobValid, scopedFormStateToConfig } from '../../utils/configForm.js';
import { invalidateSetupDoctor } from '../../utils/setupDoctorState.js';
import { humanizeError } from '../../utils/humanizeError.js';
import { taskReturnTo, returnLabel } from '../../utils/settingsNavigation.js';
import { Banner, CheckboxField, Field } from '../form/Fields.jsx';
import Button from '../ui/Button.jsx';
import ReviewDepthChoice from './ReviewDepthChoice.jsx';
import BackgroundProcessingChoice from './BackgroundProcessingChoice.jsx';
import ChecklistEngineChoice from './ChecklistEngineChoice.jsx';
import EssentialsSection from './EssentialsSection.jsx';
import UniversityAccessPanel from './UniversityAccessPanel.jsx';
import TaskPage from './TaskPage.jsx';

const TASKS = {
  selection: { title: 'Article selection', description: 'Goals and criteria for article screening. Also used to pick Telegram posts.',
    keys: ['research_goals', 'triage_criteria'], fields: ['research_goals_text', 'triage_criteria_text'], labels: ['Goals', 'Triage criteria'], save: 'Save article selection' },
  mode: { title: 'AI-assisted features', description: 'Turn AI reviews, summaries and Ask Paper on or off. ML feed ranking remains available without AI.',
    keys: ['llm_enabled'], fields: ['llm_enabled'], labels: ['AI-assisted features'], save: 'Save AI preference' },
  language: { title: 'Output language', description: 'The language requested for generated output, not the interface language or your research interests.',
    keys: ['output_language'], fields: ['output_language'], labels: ['Output language'], save: 'Save output language' },
  depth: { title: 'Review depth', description: 'How carefully the next paper review runs.',
    keys: ['review_depth'], fields: ['review_depth'], labels: ['Review depth'], save: 'Save review depth' },
  engine: { title: 'Checklist engine', description: 'Which engine answers the paper-checklist items in the next review.',
    keys: ['checklist_engine', 'checklist_local_url'], fields: ['checklist_engine', 'checklist_local_url'], labels: ['Checklist engine', 'Engine address'], save: 'Save checklist engine' },
  processing: { title: 'Background processing', description: 'How many papers are prepared at once and how far feeds are read. Applies to new work only.',
    keys: ['quality_review', 'feeds'], fields: Object.keys(PROCESSING_KNOBS), labels: ['Papers reviewed at once', 'Calls per paper', 'Feed catch-up days', 'Pages per feed'], save: 'Save background processing' },
  university: { title: 'Library access', description: 'Save this connection before signing in. Opening this task does not open a browser or read sources.',
    keys: ['university_access'], fields: ['ua_enabled', 'ua_login_url', 'ua_ezproxy_prefix', 'ua_cookie_browser'],
    labels: ['Browser fetch enabled', 'Login URL', 'EZproxy prefix', 'Browser'], save: 'Save library access' },
};
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const valueLabel = value => Array.isArray(value) ? value.join('; ') || 'None' : typeof value === 'boolean' ? value ? 'On' : 'Off' : value || 'Not set';

export default function ConfigTask({ task, backTo, backLabel }) {
  const spec = TASKS[task];
  const location = useLocation();
  const destination = backTo || taskReturnTo(location);
  const cache = useQueryClient();
  const query = useQuery({ queryKey: ['runtime-config'], queryFn: fetchConfig, retry: false });
  const [base, setBase] = useState(null);
  const [form, setForm] = useState(null);
  const [notice, setNotice] = useState('');
  const [readError, setReadError] = useState('');
  const [latest, setLatest] = useState(null);
  const [reading, setReading] = useState(false);
  const errorSummary = useRef(null);
  const formChanges = useRef(0);
  useEffect(() => {
    if (base === null && query.data) { setBase(query.data); setForm(configToFormState(query.data)); }
  }, [base, query.data]);
  const savedForm = configToFormState(base);
  const invalid = task === 'processing' && Boolean(form) && Object.keys(PROCESSING_KNOBS).some(key => !processingKnobValid(key, form[key]));
  const dirty = Boolean(form && spec.fields.some(field => !same(form[field], savedForm[field])));
  const mutation = useMutation({
    mutationFn: async submitted => {
      const response = await updateConfig(submitted.payload);
      if (response?.status !== 'ok' || typeof response.config?._revision !== 'string' || !response.config._revision || !response.config?.llm_routing
        || !spec.keys.every(key => same(response.config[key], submitted.payload[key]))) {
        throw new Error('Save not confirmed. Read saved values before any new save; the request may already have taken effect.');
      }
      return response;
    },
    onSettled: (_response, error) => invalidateSetupDoctor(cache, error),
    onSuccess: (response, submitted) => {
      const saved = response.config;
      const cached = cache.getQueryData(['runtime-config']);
      if (cached?._revision === submitted.payload._revision || cached?._revision === saved._revision) cache.setQueryData(['runtime-config'], saved);
      else cache.invalidateQueries({ queryKey: ['runtime-config'] });
      formChanges.current++;
      setBase(saved);
      setForm(current => {
        const next = configToFormState(saved);
        for (const field of spec.fields) if (!same(current[field], submitted.form[field])) next[field] = current[field];
        return next;
      });
      setNotice(`${spec.title} saved.`);
      cache.invalidateQueries({ queryKey: ['setup-status'] });
      cache.invalidateQueries({ queryKey: ['llm-connections'] });
    },
  });
  useEffect(() => { if (mutation.isError || readError) errorSummary.current?.focus(); }, [mutation.isError, readError]);
  function change(field, value) { formChanges.current++; setForm(previous => ({ ...previous, [field]: value })); setNotice(''); }
  function adopt(next, keepDraft) {
    formChanges.current++;
    const nextForm = configToFormState(next);
    setForm(current => keepDraft ? { ...nextForm, ...Object.fromEntries(spec.fields.map(field => [field, current[field]])) } : nextForm);
    setBase(next); setLatest(null); mutation.reset(); setReadError('');
    setNotice(keepDraft ? 'Saved values refreshed. Review your retained draft before saving.' : 'Current saved values loaded.');
  }
  async function refresh() {
    const revision = cache.getQueryData(['runtime-config'])?._revision;
    const changesAtRead = formChanges.current;
    setReading(true); setReadError('');
    try {
      const next = await fetchConfig();
      if (!next?._revision || !next.llm_routing) throw new Error('Saved configuration response is incomplete. No draft was replaced.');
      if (cache.getQueryData(['runtime-config'])?._revision !== revision) throw new Error('Settings changed during the read. Read again before deciding.');
      cache.setQueryData(['runtime-config'], next);
      const keepDraft = dirty || mutation.isError || formChanges.current !== changesAtRead;
      if (keepDraft && spec.keys.some(key => !same(base[key], next[key]))) setLatest(next);
      else adopt(next, keepDraft);
    } catch (error) { setReadError(humanizeError(error)); }
    finally { setReading(false); }
  }
  function submit(event) {
    event.preventDefault();
    if (!dirty || invalid || mutation.isPending || mutation.isError || latest) return;
    formChanges.current++;
    setNotice('');
    mutation.mutate({ payload: scopedFormStateToConfig(form, base, spec.keys), form: { ...form } });
  }
  return <TaskPage title={spec.title} description={spec.description} backTo={destination} backLabel={backLabel || returnLabel(destination)}
    dirty={dirty} pending={mutation.isPending}>
    {query.isPending && !form && <p role="status">Loading saved settings…</p>}
    {query.isError && !form && <Banner kind="error">Could not load saved settings. {humanizeError(query.error)}
      <Button type="button" variant="secondary" onClick={() => query.refetch()}>Retry loading settings</Button></Banner>}
    {form && <form onSubmit={submit} className="space-y-5 min-w-0">
      {notice && <Banner kind="success">{notice}</Banner>}
      {(mutation.isError || readError) && <div ref={errorSummary} tabIndex={-1} role="alert" className="rounded-xl border border-rose-200 bg-rose-50 p-4 space-y-3 [overflow-wrap:anywhere]">
        <p>{mutation.error ? `Save failed: ${humanizeError(mutation.error)}` : readError}</p>
        {readError && mutation.error && <p>{readError}</p>}
        <Button type="button" variant="secondary" disabled={reading || mutation.isPending} onClick={refresh}>Read saved values; keep draft</Button>
      </div>}
      {!mutation.isError && base._revision !== query.data?._revision && <Banner kind="info">Saved settings changed elsewhere. Your draft still uses its original revision.
        <Button type="button" variant="secondary" disabled={reading || mutation.isPending} onClick={refresh}>Read saved values; keep draft</Button></Banner>}
      {latest && <section aria-label="Review changed saved values" className="rounded-xl border border-amber-300 bg-amber-50 p-4 space-y-4">
        <h2 className="font-display text-xl font-light">Saved values changed while you were editing</h2>
        <p className="text-sm">Compare before deciding. Nothing is saved by either choice.</p>
        <dl className="space-y-3 text-sm [overflow-wrap:anywhere]">{spec.fields.map((field, index) => <div key={field}>
          <dt className="font-medium">{spec.labels[index]}</dt>
          <dd>Saved now: {valueLabel(configToFormState(latest)[field])}</dd><dd>Your draft: {valueLabel(form[field])}</dd>
        </div>)}</dl>
        <div className="flex flex-wrap gap-3">
          <Button type="button" variant="secondary" onClick={() => adopt(latest, true)}>Keep my draft for these saved values</Button>
          <Button type="button" variant="secondary" onClick={() => adopt(latest, false)}>Use saved values instead</Button>
        </div>
      </section>}
      {task === 'selection' && <EssentialsSection form={form} onUpdate={change} tidied={(base?.research_goals || []).filter(goal => cleanGoal(goal) !== goal).length} />}
      {task === 'mode' && <CheckboxField label="Enable AI-assisted features" checked={form.llm_enabled} onChange={value => change('llm_enabled', value)} />}
      {task === 'language' && <Field label="Output language" value={form.output_language} onChange={value => change('output_language', value)} hint="Blank uses English." />}
      {task === 'depth' && <ReviewDepthChoice value={form.review_depth} onChange={value => change('review_depth', value)} />}
      {task === 'engine' && <ChecklistEngineChoice value={form.checklist_engine} url={form.checklist_local_url} onChange={value => change('checklist_engine', value)} onUrl={value => change('checklist_local_url', value)} />}
      {task === 'processing' && <BackgroundProcessingChoice form={form} onChange={change} />}
      {task === 'university' && <UniversityAccessPanel form={form} savedForm={savedForm} saving={mutation.isPending || mutation.isError || Boolean(latest) || base._revision !== query.data?._revision} onUpdate={change} />}
      <div className="flex flex-wrap items-center gap-3 border-t border-slate-200 pt-4">
        <Button type="submit" disabled={!dirty || invalid || mutation.isPending || mutation.isError || Boolean(latest)}>{mutation.isPending ? 'Saving…' : spec.save}</Button>
        {dirty && <Button type="button" variant="secondary" disabled={mutation.isPending || mutation.isError} onClick={() => { formChanges.current++; setForm(savedForm); setNotice('Draft discarded; saved configuration is unchanged.'); }}>Discard draft</Button>}
        <span className="text-sm text-slate-600" role="status">{dirty && 'Unsaved draft — only this task will be saved.'}</span>
      </div>
    </form>}
  </TaskPage>;
}
