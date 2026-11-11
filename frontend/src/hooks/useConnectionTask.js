import { useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { fetchConnections, registerConnection, listConnectionModels, previewConnection, applyConnection, readConnection, testConnection } from '../api/connectionsApi.js';
import { invalidateSetupDoctor } from '../utils/setupDoctorState.js';

export function useConnectionTask(onSaved) {
  const client = useQueryClient();
  const query = useQuery({ queryKey: ['llm-connections'], queryFn: fetchConnections, retry: false, refetchOnWindowFocus: false });
  const owner = useRef(0);
  const pending = useRef(false);
  const [task, setTask] = useState({});
  const unresolvedTest = useRef(null);
  useEffect(() => () => { owner.current++; }, []);
  function retire() { owner.current++; setTask({ busy: pending.current }); }
  async function publish(active, previousRevision, outcomeRevision) {
    const current = await fetchConnections();
    if (!active()) return;
    const admitted = () => [client.getQueryData(['llm-connections'])?.revision, client.getQueryData(['runtime-config'])?._revision]
      .every(revision => revision == null || revision === previousRevision || revision === current.revision);
    async function changed() {
      owner.current++;
      setTask({ busy: pending.current, error: 'Saved settings changed. Start a fresh connection approval. Earlier writes are not undone; no request was replayed.' });
      await Promise.all([client.invalidateQueries({ queryKey: ['llm-connections'], refetchType: 'all' }),
        client.invalidateQueries({ queryKey: ['runtime-config'], refetchType: 'all' })]);
      await invalidateSetupDoctor(client);
    }
    if (current.revision !== outcomeRevision || !admitted()) return changed();
    await client.cancelQueries({ queryKey: ['llm-connections'] }, { revert: false });
    await client.cancelQueries({ queryKey: ['runtime-config'] }, { revert: false });
    if (!active()) return;
    if (!admitted()) return changed();
    client.setQueryData(['llm-connections'], current);
    // A saved address/assignment is a new thing to probe: never keep showing the old provider status.
    ['llm-reachability', 'deep-review-reachability'].forEach(key => client.invalidateQueries({ queryKey: [key] }));
    if (current.config) onSaved?.({ ...current.config, _revision: current.revision }, { previousRevision });
    await invalidateSetupDoctor(client);
    return current;
  }
  async function run(work) {
    if (pending.current) return;
    pending.current = true;
    const token = ++owner.current;
    const active = () => token === owner.current;
    setTask(prev => ({ ...prev, busy: true, error: '' }));
    try { await work(active); }
    catch (error) {
      if (active()) setTask(prev => ({ ...prev, error: error.message, preview: null, impact: null,
        uncertain: Boolean(prev.uncertain || ['registration', 'selection'].includes(prev.effect)) }));
    }
    finally {
      pending.current = false;
      setTask(prev => ({ ...prev, busy: false }));
    }
  }
  function connect(draft) {
    return run(async active => {
      const command = { ...draft, revision: draft.revision, operation_id: crypto.randomUUID() };
      setTask({ operation_id: command.operation_id, revision: command.revision, busy: true, effect: 'registration' });
      const registered = await registerConnection(command);
      if (!active()) return;
      setTask({ ...registered, busy: true, effect: 'catalog' });
      // Refresh the parent saved routing/revision without resetting unrelated drafts.
      await publish(active, command.revision, registered.revision);
      if (!active()) return;
      const catalog = await listConnectionModels(command.operation_id);
      if (active()) setTask({ ...registered, ...catalog });
    });
  }
  function preview(model, corrections = {}, targets = ['default']) {
    return run(async active => {
      setTask(prev => ({ ...prev, effect: 'preview', preview: null }));
      const result = await previewConnection({ operation_id: task.operation_id, revision: task.revision, model, corrections, targets });
      if (active()) setTask(prev => ({ ...prev, impact: result.impact, preview: result.preview, ready: result.ready_to_apply, model, corrections, targets }));
    });
  }
  function apply() {
    return run(async active => {
      setTask(prev => ({ ...prev, effect: 'selection' }));
      const result = await applyConnection({ operation_id: task.operation_id, revision: task.revision, model: task.model, targets: task.targets || ['default'], preview: task.preview, corrections: task.corrections || {} }, task.impact);
      if (!active()) return;
      await publish(active, task.revision, result.revision);
      if (active()) setTask(prev => ({ ...prev, ...result, saved: true }));
    });
  }
  function reconcile() {
    return run(async active => {
      setTask(prev => ({ ...prev, effect: 'readback' }));
      const result = await readConnection(task.operation_id);
      if (!active()) return;
      await publish(active, task.revision, result.revision);
      if (active()) setTask(prev => ({ ...prev, ...result, busy: false, uncertain: false,
        saved: result.selection_matches, preview: null,
        error: result.current_revision !== result.revision ? 'State changed. Start a fresh connection approval; the saved registration remains.' : '',
        reconciled: true }));
    });
  }
  function authorizationSaved(receipt, { previousRevision } = {}) {
    return run(async active => {
      // Credential writes retire prior approvals, but only their exact receipt
      // can advance the parent draft. Refusal/manual inspection is a read, not a save.
      setTask({ busy: true });
      if (receipt?.state === 'auth_stored') await publish(active, previousRevision, receipt.revision);
      else await Promise.all([client.invalidateQueries({ queryKey: ['llm-connections'] }),
        client.invalidateQueries({ queryKey: ['runtime-config'] })]);
    });
  }
  function catalog() {
    return run(async active => {
      setTask(prev => ({ ...prev, effect: 'catalog' }));
      const result = await listConnectionModels(task.operation_id);
      if (active()) setTask(prev => ({ ...prev, ...result, preview: null, impact: null }));
    });
  }
  function testModel(stages) {
    return run(async active => {
      setTask(prev => ({ ...prev, effect: 'test', test: null }));
      const result = await testConnection({ revision: query.data.revision, ...(Array.isArray(stages)
        ? { stages } : { model: query.data.config.llm_routing.default.model }) });
      if (result.hold || result.ownership === 'unresolved' || result.attempt?.hold) unresolvedTest.current = result;
      if (active()) setTask(prev => ({ ...prev, test: result }));
    });
  }
  function editModel() {
    owner.current++;
    setTask(prev => ({ ...prev, preview: null, impact: null, error: '', saved: false, test: null }));
  }
  function editCorrections() {
    owner.current++;
    setTask(prev => ({ ...prev, preview: null, ready: false, error: '', saved: false, test: null }));
  }
  return { query, task: { ...task, test: task.test || unresolvedTest.current }, retire, connect, preview, apply, reconcile, authorizationSaved, catalog, editModel, editCorrections, testModel };
}
