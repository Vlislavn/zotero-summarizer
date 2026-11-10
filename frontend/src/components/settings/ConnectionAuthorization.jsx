import { useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { readConnectionCredential, replaceConnectionCredential } from '../../api/connectionsApi.js';
import { invalidateSetupDoctor } from '../../utils/setupDoctorState.js';
import Button from '../ui/Button.jsx';
import { Field, Banner } from '../form/Fields.jsx';
import { connectionLabel, connectionTaskLabel } from '../../utils/connectionTaskLabels.js';

export default function ConnectionAuthorization({ providers, revision, onSaved, disabled, connectionId, onActivityChange }) {
  const client = useQueryClient();
  const [provider, setProvider] = useState(connectionId ? providers[0]?.name || '' : '');
  const [preview, setPreview] = useState(null);
  const [secret, setSecret] = useState('');
  const [state, setState] = useState('');
  const [busy, setBusy] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const [receipt, setReceipt] = useState(null);
  const alive = useRef(true);
  const dirty = Boolean(secret || preview || uncertain);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    onActivityChange?.({ dirty, pending: busy });
    return () => onActivityChange?.({ dirty: false, pending: false });
  }, [busy, dirty, onActivityChange]);
  async function notifySaved(result, previousRevision, expectedProvider) {
    if (result?.state === 'auth_stored' && (typeof result.revision !== 'string' || !result.revision
      || result.provider !== expectedProvider || result.credential?.present !== true)) throw new Error('Credential write not confirmed.');
    if (alive.current) await onSaved?.(result, { previousRevision });
  }
  const choices = providers.filter(p => p.auth_mode !== 'none');
  if (!choices.length) return null;
  async function review() {
    setBusy(true); setState('');
    try {
      const result = await replaceConnectionCredential({ provider, revision, ...(connectionId ? { connection_id: connectionId } : {}), operation_id: crypto.randomUUID() });
      if (typeof result?.preview !== 'string' || result.revision !== revision || !Array.isArray(result.impact?.consumers)) throw new Error('Authorization impact unavailable.');
      setPreview(result);
    } catch (error) { setState(error.message); }
    finally { setBusy(false); }
  }
  async function replace() {
    setBusy(true); setState('');
    try {
      setReceipt({ token: preview.preview, previousRevision: revision, provider });
      const result = await replaceConnectionCredential({ provider, revision, ...(connectionId ? { connection_id: connectionId } : {}), operation_id: preview.operation_id, preview: preview.preview, secret });
      if (result?.state !== 'auth_stored' || result.credential?.present !== true) throw new Error('Credential write not confirmed.');
      setState('Authorization stored separately. Earlier catalogs and previews are no longer current.');
      setSecret(''); setPreview(null); await notifySaved(result, revision, provider);
    } catch (error) {
      setState(`${error.message} Credential outcome may be unknown. Do not blindly retry or assume Cancel rolls it back.`);
      setSecret(''); setPreview(null); setUncertain(true);
    } finally { setBusy(false); await invalidateSetupDoctor(client); }
  }
  return <details className="text-sm rounded-xl border border-slate-200 p-3">
    <summary className="cursor-pointer">{connectionId ? 'Add an API key for this connection' : 'Manage saved API keys'}</summary>
    <p className="mt-2 text-xs">API keys are stored separately from model choices. Review the affected connections and tasks before saving a key. An active verification or Telegram job may block replacement. Cancel does not undo a key already stored.</p>
    <Field kind="select" label={connectionId ? 'Connection needing an API key' : 'Connection to update'} value={provider} disabled={busy || disabled || uncertain}
      options={[{ value: '', label: 'Choose a saved server' }, ...choices.map(p => ({ value: p.name, label: connectionLabel(p) }))]}
      onChange={value => { setProvider(value); setPreview(null); setSecret(''); }} />
    <Button type="button" variant="secondary" disabled={!provider || busy || disabled || uncertain} onClick={review}>Review authorization impact</Button>
    {preview && preview.revision === revision && <div className="space-y-2 mt-2">
      <p>Shared servers: {preview.impact.servers?.join(', ') || 'selected saved servers'}. Consumers: {preview.impact.consumers.map(connectionTaskLabel).join(', ') || 'none'}.</p>
      <label className="block">{connectionId ? 'API key' : 'New API key'}<input type="password" autoComplete="off" value={secret} disabled={busy || disabled || uncertain} onChange={e => setSecret(e.target.value)} className="block w-full border rounded-lg p-2" /></label>
      <Button type="button" disabled={!secret || busy || disabled} onClick={replace}>{connectionId ? 'Store API key' : 'Replace saved API key'}</Button>
    </div>}
    {state && <Banner kind="info">{state}</Banner>}
    {uncertain && receipt && <Button type="button" variant="secondary" disabled={busy} onClick={async () => {
      setBusy(true);
      try {
        const result = await readConnectionCredential(receipt.token);
        setState(result?.state === 'auth_stored' ? 'Readback confirms authorization stored separately.'
          : result?.state === 'auth_refused' ? 'Replacement was refused before credential storage. Review current consumers before a new explicit replacement.'
          : 'Authorization outcome remains unknown; inspect the OS credential store before any new explicit replacement.');
        if (['auth_stored', 'auth_refused'].includes(result?.state)) {
          await notifySaved(result, receipt.previousRevision, receipt.provider); setUncertain(false);
        }
      } catch (error) { setState(error.message); }
      finally { setBusy(false); }
    }}>Read authorization receipt</Button>}
    {uncertain && <Button type="button" variant="secondary" disabled={busy} onClick={async () => {
      setPreview(null); setSecret(''); await notifySaved(); setUncertain(false);
      setState('Prior outcome remains unknown. After inspecting the OS store, review current consumers before any deliberate new replacement; no write has been retried.');
    }}>I inspected the OS store; start a new review</Button>}
  </details>;
}
