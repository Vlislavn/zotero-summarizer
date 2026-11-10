import { useState } from 'react';
import { CHECKLIST_ENGINES, CLOUD_CONSENT, GAIN_DETAILS, GAIN_LINE, LOCAL_NOTICE, LOCAL_URL } from '../../utils/checklistEngine.js';
import { fetchChecklistEngine, testChecklistEngine } from '../../api/connectionsApi.js';
import { useQuery } from '@tanstack/react-query';
import Button from '../ui/Button.jsx';
import { Disclosure } from '../paper/review/primitives.jsx';
import { humanizeError } from '../../utils/humanizeError.js';

const INPUT = 'w-full mt-1 p-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-teal-500';

export default function ChecklistEngineChoice({ value, url, onChange, onUrl }) {
  const [secret, setSecret] = useState('');
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const status = useQuery({ queryKey: ['checklist-engine'], queryFn: fetchChecklistEngine, retry: false });
  const keyPresent = result?.key_present ?? status.data?.key_present;
  async function test() {
    setBusy(true); setResult(null);
    try {
      const answer = await testChecklistEngine({ engine: value, url: url || LOCAL_URL, secret: value === 'cloud' && secret ? secret : undefined });
      setResult(answer); setSecret('');
    } catch (error) { setResult({ ok: false, message: humanizeError(error) }); }
    finally { setBusy(false); }
  }
  return <fieldset className="space-y-3 min-w-0">
    <legend className="sr-only">Checklist engine</legend>
    <p className="text-sm text-slate-700">{GAIN_LINE}</p>
    <div className="[&_summary]:min-h-11"><Disclosure summary="What was measured">
      <ul className="list-disc space-y-1 pl-5 text-sm text-slate-600">{GAIN_DETAILS.map(line => <li key={line}>{line}</li>)}</ul></Disclosure></div>
    {Object.entries(CHECKLIST_ENGINES).map(([key, option]) => <label key={key}
      className="flex min-h-11 cursor-pointer items-start gap-3 rounded-xl border border-slate-200 p-4 has-[:checked]:border-forest-800 has-[:checked]:bg-linen">
      <input type="radio" name="checklist_engine" value={key} checked={value === key} onChange={() => { onChange(key); setResult(null); }} className="mt-1" />
      <span className="min-w-0"><span className="block text-base font-medium text-slate-900">{option.label}{key === 'off' && ' (default)'}</span>
        <span className="block text-sm text-slate-600 first-letter:uppercase">{option.detail}</span></span>
    </label>)}
    {value === 'local' && <div className="space-y-2">
      <label className="block text-sm font-medium text-slate-800">Engine address
        <input type="url" className={INPUT} value={url} onChange={event => { onUrl(event.target.value); setResult(null); }} placeholder={LOCAL_URL} /></label>
      <p className="text-sm text-slate-600">{LOCAL_NOTICE}</p>
    </div>}
    {value === 'cloud' && <div className="space-y-2">
      <p className="text-sm text-slate-700">{CLOUD_CONSENT}</p>
      {keyPresent ? <p className="text-sm text-slate-600">An access key is saved on this computer.</p> : <p className="text-sm text-amber-800">No access key saved yet.</p>}
      <label className="block text-sm font-medium text-slate-800">{keyPresent ? 'Replace access key (optional)' : 'Access key'}
        <input type="password" autoComplete="off" className={INPUT} value={secret} onChange={event => setSecret(event.target.value)} /></label>
    </div>}
    {value !== 'off' && <div className="flex flex-wrap items-center gap-3">
      <Button type="button" variant="secondary" disabled={busy} onClick={test}>{busy ? 'Testing…' : 'Test'}</Button>
      {result && <span role="status" className={`text-sm ${result.ok ? 'text-forest-800' : 'text-rose-700'}`}>{result.ok ? '✓ ' : ''}{result.message}</span>}
    </div>}
    <p className="text-sm text-slate-600">When the engine is unsure or unavailable, the language model answers instead.</p>
  </fieldset>;
}
