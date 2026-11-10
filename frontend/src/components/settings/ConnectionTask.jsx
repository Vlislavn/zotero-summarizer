import { useEffect, useRef, useState } from 'react';
import { useConnectionTask } from '../../hooks/useConnectionTask.js';
import { isLocalProvider, resolveStage } from '../../utils/configForm.js';
import { connectionLabel, connectionTaskLabel } from '../../utils/connectionTaskLabels.js';
import { Field, Banner } from '../form/Fields.jsx';
import Button from '../ui/Button.jsx';
import ConnectionImpact from './ConnectionImpact.jsx';
import ConnectionAuthorization from './ConnectionAuthorization.jsx';
import ConnectionCorrections from './ConnectionCorrections.jsx';
import ConnectionTestResult from './ConnectionTestResult.jsx';
import ConnectionOptions from './ConnectionOptions.jsx';
import { ConnectionScope } from './ConnectionUsage.jsx';
import StepModelsTable from './StepModelsTable.jsx';

const EMPTY = { type: 'openai', base_url: 'http://localhost:11434/v1', auth: 'none' };
function draftFor(source) {
  return source ? { type: source.type, base_url: source.base_url || '', auth: source.auth_mode === 'none' ? 'none' : 'existing',
    authorization: source.name, source: source.name } : EMPTY;
}
export default function ConnectionTask({ onSaved, disabled = false, autoOpen = false, onClose, onEditingChange, onActivityChange, onTestChange, suggestion, advancedSteps = false }) {
  const workflow = useConnectionTask(onSaved);
  const { query, task } = workflow;
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(EMPTY);
  const [model, setModel] = useState('');
  const [manual, setManual] = useState(false);
  const [targets, setTargets] = useState(['default']);
  const [corrections, setCorrections] = useState({});
  const initialDraft = useRef(EMPTY);
  const initialModel = useRef('');
  const initialTargets = useRef(['default']);
  const [connectionAuthorization, setConnectionAuthorization] = useState({ dirty: false, pending: false });
  const [existingAuthorization, setExistingAuthorization] = useState({ dirty: false, pending: false });
  const changedDraft = Object.keys({ ...initialDraft.current, ...draft }).some(key => (draft[key] ?? '') !== (initialDraft.current[key] ?? ''));
  const dirty = Boolean(editing && (changedDraft || model !== initialModel.current || targets.join() !== initialTargets.current.join() || task.operation_id && !task.saved)
    || connectionAuthorization.dirty || existingAuthorization.dirty);
  const pending = Boolean(task.busy || connectionAuthorization.pending || existingAuthorization.pending || task.test?.hold || task.test?.ownership === 'unresolved');
  const adoptedOpen = useRef(false);
  const adoptedSuggestion = useRef(null);
  const providers = query.data?.providers || [];
  const routing = query.data?.config?.llm_routing;
  const current = providers.find(p => p.name === routing?.default?.provider);
  const source = providers.find(p => p.name === draft.source);
  const reusing = source && source.type === draft.type && (source.base_url || '') === draft.base_url
    && (draft.auth === 'none' && source.auth_mode === 'none' || draft.auth === 'existing' && draft.authorization === source.name);
  const test = task.test && { ...task.test, current: task.test.current === true
    && typeof query.data?.revision === 'string' && task.test.revision === query.data.revision };
  const catalogNeedsFirstModel = Boolean(task.bootstrap && task.state === 'draft' && task.reason && !task.detail);
  useEffect(() => { onEditingChange?.(editing); return () => onEditingChange?.(false); }, [editing, onEditingChange]);
  useEffect(() => { onActivityChange?.({ dirty, pending }); }, [dirty, pending, onActivityChange]);
  useEffect(() => { onTestChange?.(test); }, [test, onTestChange]);
  useEffect(() => {
    if (!suggestion?.provider || suggestion === adoptedSuggestion.current) return;
    adoptedSuggestion.current = suggestion;
    workflow.retire();
    setDraft({ type: suggestion.provider.type, base_url: suggestion.provider.base_url || '', auth: 'none' });
    setModel(suggestion.model || ''); setManual(true); setTargets(['default']); setCorrections({}); setEditing(true);
  }, [suggestion, workflow]);
  useEffect(() => {
    if (!autoOpen) { adoptedOpen.current = false; return; }
    if (adoptedOpen.current || editing || !query.data) return;
    adoptedOpen.current = true;
    initialDraft.current = draftFor(current); initialModel.current = routing?.default?.model || ''; initialTargets.current = ['default'];
    setDraft(initialDraft.current); setModel(initialModel.current); setTargets(['default']); setEditing(true);
  }, [autoOpen, current, editing, query.data, routing]);
  useEffect(() => {
    if (task.saved) { setEditing(false); onClose?.(); }
  }, [task.saved, onClose]);
  useEffect(() => {
    if (editing && task.provider && model.trim() && targets.length && !task.busy && !task.preview && !task.error && !task.uncertain && !task.saved) {
      workflow.preview(model, corrections, targets);
    }
  }, [editing, model, targets, corrections, task, workflow]);
  function change(fields) {
    workflow.retire(); setDraft(prev => ({ ...prev, ...fields })); setModel(''); setCorrections({});
  }
  function start(source, target) {
    const chosen = target ? resolveStage(routing, target).model || '' : '';
    if (!editing) {
      initialDraft.current = draftFor(source); initialModel.current = chosen; initialTargets.current = [target || 'default'];
    }
    workflow.retire(); setEditing(true); setModel(chosen); setManual(false); setCorrections({}); setDraft(draftFor(source));
    if (target || !editing) setTargets([target || 'default']);
  }
  function chooseModel(value) {
    setModel(value); setCorrections({}); workflow.editModel();
  }
  const metadataAuth = draft.auth === 'new' ? 'a new API key stored separately; no existing key is used' : draft.auth === 'none' ? 'no key' : isLocalProvider(draft)
    ? 'a non-secret local discovery token; the saved key is reserved for inference' : `saved access for ${providers.find(p => p.name === draft.authorization)?.base_url || 'native Anthropic'}`;
  if (query.isPending) return <p>Loading saved models…</p>;
  if (query.isError) return <Banner kind="error">Saved models unavailable. <Button type="button" onClick={() => query.refetch()}>Refresh connections</Button></Banner>;
  const table = { routing, test, testing: Boolean(task.busy && task.effect === 'test'), onChange: start, onTest: workflow.testModel, disabled, testDisabled: disabled || pending };
  return <div className="space-y-3 min-w-0">
    <h3 className="font-display text-lg font-light">{editing ? 'Choose a model' : 'Model for each step'}</h3>
    {!editing && <>
      {advancedSteps ? <>
        <StepModelsTable {...table} summary />
        <details className="rounded-xl border border-slate-200 px-3"><summary className="min-h-11 cursor-pointer py-2.5 text-sm font-medium">Advanced: a different model per step</summary>
          <StepModelsTable {...table} /></details>
      </> : <StepModelsTable {...table} showFallback />}
      <Button type="button" variant="secondary" disabled={disabled} onClick={() => start()}>Add provider</Button>
    </>}
    {editing && current && targets.includes('default') && <div className="space-y-2 rounded-xl bg-linen p-3">
      <p className="text-xs text-slate-600 break-words">Saved default check: {routing.default.model} · {connectionLabel(current)}. This does not test your draft or task-specific models.</p>
      <Button type="button" variant="secondary" disabled={disabled || pending} onClick={() => workflow.testModel()}>Test saved model</Button>
    </div>}
    {editing && <ConnectionTestResult result={test} onRepair={target => start(resolveStage(routing, target || 'default').provider, target || 'default')} />}
    {!editing && task.error && <Banner kind="error">{task.error}</Banner>}
    {task.saved && <div className="space-y-2">
      <p role="status" className="text-sm">Selection saved. Saving did not run a model. Check the saved assignments above.</p>
      {task.targets?.some(target => target !== 'default') && <>
        <p className="text-xs text-slate-600">Send a small test prompt to: {task.targets.filter(target => target !== 'default').map(connectionTaskLabel).join(', ')}. The default has its separate check.</p>
        <Button type="button" variant="secondary" disabled={disabled || pending} onClick={() => workflow.testModel(task.targets.filter(target => target !== 'default'))}>Test selected tasks</Button>
      </>}
    </div>}
    {editing && <fieldset className="space-y-3 rounded-xl border border-slate-200 p-3 min-w-0" disabled={disabled}>
      <legend className="px-1 text-sm">Provider and model</legend>
      {!task.provider && <>
        {providers.length > 0 && <Field kind="select" label="Saved provider" value={draft.source || ''}
          options={[{ value: '', label: 'Add a new provider' }, ...providers.map(p => ({ value: p.name, label: connectionLabel(p) }))]}
          onChange={name => start(providers.find(p => p.name === name))} />}
        {!draft.source && <Field label="API address" value={draft.base_url || ''} onChange={base_url => change({ base_url })} />}
        <ConnectionOptions draft={draft} providers={providers} onChange={change} />
        {model && <p className="text-sm">Currently saved model: {model}</p>}
        <p className="text-xs break-words">{reusing ? 'Reuse this connection and fetch model names' : 'Save an unused connection and fetch model names'} from {draft.base_url || 'https://api.anthropic.com'} with {metadataAuth}. Current assignments stay unchanged. No model is run.</p>
        <Button type="button" variant="secondary" disabled={task.busy || task.uncertain} onClick={() => workflow.connect({ ...draft, approved: true, revision: query.data.revision })}>
          {task.busy ? 'Loading model names…' : reusing ? 'Load models' : 'Connect provider'}
        </Button>
      </>}
      {task.provider && <>
        <p role="status" className="text-sm">{task.bootstrap ? 'Connection draft retained; no active model yet.' : task.reused ? 'Saved connection reused. Assignments have not changed.' : 'Connection saved but not used yet. Assignments have not changed.'}</p>
        <p className="text-sm break-words">{connectionLabel(task.provider)} · {task.provider.auth_mode === 'none' ? 'No key is used.' : task.auth === 'stored' ? 'API access stored.' : 'API access not yet confirmed.'}</p>
        {task.authorization_new && task.provider.auth_mode !== 'none' && <ConnectionAuthorization
          providers={[task.provider]} revision={task.revision} connectionId={task.operation_id}
          onSaved={workflow.reconcile} onActivityChange={setConnectionAuthorization} disabled={disabled || task.busy} />}
        {task.catalog === 'unavailable' && <div className="text-sm space-y-2">
          <p>{task.detail || task.reason || 'The model list is unavailable. Enter a model name or retry discovery.'}</p>
          {catalogNeedsFirstModel ? <p>After saving the first model, use Change to fetch the model list.</p>
            : <Button type="button" variant="secondary" disabled={task.busy || task.uncertain} onClick={workflow.catalog}>Retry model catalog</Button>}
        </div>}
        {!manual && task.models?.length ? <>
          <Field kind="select" label="Model" value={model} disabled={task.busy && task.effect !== 'preview' || task.uncertain}
            options={[{ value: '', label: 'Choose a returned model' }, ...(model && !task.models.includes(model) ? [{ value: model, label: `${model} (saved; not in this list)` }] : []), ...task.models.map(id => ({ value: id, label: id }))]} onChange={chooseModel} />
          <Button type="button" variant="ghost" onClick={() => { chooseModel(''); setManual(true); }}>Enter a model name manually</Button>
        </> : <Field label="Model" value={model} disabled={task.busy && task.effect !== 'preview' || task.uncertain} onChange={chooseModel} />}
        <ConnectionScope routing={routing} targets={targets} disabled={task.busy && task.effect !== 'preview' || task.uncertain} onChange={next => {
          setTargets(next); setCorrections({}); workflow.editCorrections();
        }} />
        {task.busy && task.effect === 'preview' && <p role="status" className="text-xs">Calculating proposed changes… Nothing applied yet.</p>}
        <ConnectionImpact impact={task.impact} />
        <ConnectionCorrections stages={task.impact?.required_corrections} choices={corrections}
          onChange={(stage, choice) => { setCorrections(prev => {
            const next = { ...prev }; if (choice) next[stage] = choice; else delete next[stage]; return next;
          }); workflow.editCorrections(); }} />
        {task.preview && task.ready && targets.length > 0 && <Button type="button" disabled={task.busy || task.uncertain} onClick={workflow.apply}>
          {targets.length === 1 && targets[0] === 'default' ? 'Use as default' : 'Use for selected tasks'}
        </Button>}
      </>}
      {task.error && <Banner kind="error">{task.error}</Banner>}
      {task.operation_id && (task.error || task.uncertain) && <Button type="button" variant="secondary" disabled={task.busy} onClick={workflow.reconcile}>Read back intended connection</Button>}
      {task.reconciled && <p role="status">{task.bootstrap ? 'Connection draft retained; not active yet.' : `Readback: ${task.provider_matches ? 'exact server registered' : 'registration not confirmed'}; ${task.selection_matches ? 'exact selection saved' : 'selection not confirmed'}.`}</p>}
      <p className="text-xs text-slate-600">Cancel discards these choices, not already saved connections or API keys. It does not stop a request already sent.</p>
      <Button type="button" variant="secondary" onClick={() => { workflow.retire(); setEditing(false); onClose?.(); }}>Cancel</Button>
    </fieldset>}
    <ConnectionAuthorization providers={providers} revision={query.data?.revision} onSaved={workflow.authorizationSaved} onActivityChange={setExistingAuthorization} disabled={disabled || task.busy} />
  </div>;
}
