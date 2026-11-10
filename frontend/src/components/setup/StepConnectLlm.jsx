// Setup uses the same persisted connection task as ordinary maintenance.
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { fetchAiPresets } from '../../api/setupApi.js';
import ConnectionTask from '../settings/ConnectionTask.jsx';
import Button from '../ui/Button.jsx';
import { Banner } from '../form/Fields.jsx';

export default function StepConnectLlm({ mode, onSaved }) {
  const [suggestion, setSuggestion] = useState(null);
  const [test, setTest] = useState(null);
  const catalog = useQuery({ queryKey: ['ai-presets'], queryFn: fetchAiPresets, enabled: mode === 'local' });
  const local = catalog.data?.local_profiles;
  return <div className="space-y-4">
    <h3 className="font-medium">Connect AI</h3>
    <p className="text-sm">Connection changes save separately from research and paths. Connect selects a saved server or registers a new inactive destination; review impact before Use as default. Existing exceptions and Telegram stay independent.</p>
    {mode === 'local' && local && <details className="rounded-xl border border-slate-200 p-3">
      <summary>Local profile suggestions — not load approval</summary>
      <p className="text-xs">Static memory/disk floors are advisory. Preparing a suggestion changes only this connection draft; it never downloads, tests or replaces existing routes.</p>
      {local.profiles.map(profile => <div key={profile.id} className="py-2 space-y-1 text-xs">
        <Button type="button" variant="secondary" disabled={!profile.compatible}
          onClick={() => setSuggestion({ provider: profile.provider, model: profile.model || '' })}>{profile.label}</Button>
        <p>{profile.model || 'Your explicit model'}{profile.size_gb ? ` · approximately ${profile.size_gb} GB` : ''}. {profile.compatibility_detail}</p>
        {profile.source && <a className="underline" href={profile.source} target="_blank" rel="noreferrer">Official model source</a>}
        {profile.pull_command && <p>Download only by an explicit operator command: <code>{profile.pull_command}</code></p>}
      </div>)}
    </details>}
    {catalog.isError && mode === 'local' && <Banner kind="error">Local profile compatibility is unavailable. Metadata/manual model IDs remain available; no load is authorized.</Banner>}
    <ConnectionTask onSaved={onSaved} onTestChange={setTest} suggestion={suggestion} advancedSteps />
    {test?.status !== 'operational' && !test?.targets && <p role="status" className="rounded-xl bg-amber-50 p-3 text-sm text-amber-900">
      No model has answered a test prompt yet. Press Test next to a step before you finish — setup is only verified on the last screen.</p>}
  </div>;
}
