// Settings → "Deployment". The one genuinely user-owned routing decision (Tesler's Law:
// privacy/cost vs quality): run everything on your LOCAL model, or HYBRID (cheap high-volume
// stages local, the deep review on the remote API model). Presets-first (Hick's Law — not a raw
// stage×provider matrix); "Custom" points at the AI-models routing editor below. A "Measure
// my setup" button surfaces which stages are token/compute-heavy so the choice is informed.

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { fetchProfiles, measureProfile, setProfile } from '../../api/settingsApi.js';
import { humanizeError } from '../../utils/humanizeError.js';
import { Banner, SectionCard } from '../form/Fields.jsx';
import Button from '../ui/Button.jsx';

export default function DeploymentCard() {
  const queryClient = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ['profiles'], queryFn: fetchProfiles });
  const [measure, setMeasure] = useState(null);
  const [error, setError] = useState('');

  const applyMutation = useMutation({
    mutationFn: (profile) => setProfile(profile),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['profiles'] });
      queryClient.invalidateQueries({ queryKey: ['runtime-config'] });
    },
    onError: (e) => setError(humanizeError(e)),
  });

  const measureMutation = useMutation({
    mutationFn: () => measureProfile({ includeLocal: false }),
    onSuccess: (res) => { setMeasure(res); setError(''); },
    onError: (e) => setError(humanizeError(e)),
  });

  if (isLoading || !data) {
    return (
      <SectionCard title="Deployment" description="Where each LLM stage runs.">
        <p className="text-xs text-slate-500">Loading…</p>
      </SectionCard>
    );
  }

  const { profiles, current } = data;
  const rec = measure?.recommendation;

  return (
    <SectionCard
      title="Deployment"
      description="Choose where each LLM stage runs. Fully local is private + free but reviews are shallower; hybrid keeps cheap stages local and runs the deep review on the remote API model."
    >
      <div className="space-y-2">
        {Object.entries(profiles).map(([name, p]) => (
          <label
            key={name}
            className={`flex gap-3 rounded-xl border p-3 cursor-pointer ${
              current === name ? 'border-forest-800 bg-forest-800/5' : 'border-slate-200 hover:bg-slate-50'
            }`}
          >
            <input
              type="radio"
              name="deployment-profile"
              checked={current === name}
              onChange={() => applyMutation.mutate(name)}
              disabled={applyMutation.isPending}
              className="mt-1"
            />
            <span>
              <span className="text-sm font-semibold text-slate-800">
                {p.label}
                {current === name && <span className="ml-2 text-xs text-forest-800">active</span>}
                {rec?.profile === name && <span className="ml-2 text-xs text-emerald-700">recommended</span>}
              </span>
              <span className="block text-xs text-slate-500 mt-0.5">{p.description}</span>
            </span>
          </label>
        ))}
        <p className="text-xs text-slate-400">
          {current === 'custom' && 'Custom routing active — '}
          For per-stage control, use the AI models editor above.
        </p>
      </div>

      <div className="flex items-center gap-3 pt-1">
        <Button type="button" variant="secondary" onClick={() => measureMutation.mutate()} disabled={measureMutation.isPending}>
          {measureMutation.isPending ? 'Measuring… (real LLM calls)' : 'Measure my setup'}
        </Button>
        {rec && <span className="text-xs text-slate-600">{rec.rationale}</span>}
      </div>

      {measure && (
        <div className="text-xs text-slate-600 overflow-x-auto">
          <table className="mt-1 border-collapse">
            <thead>
              <tr className="text-slate-400">
                <th className="text-left pr-4">stage</th><th className="text-left pr-4">provider</th>
                <th className="text-right pr-4">~tokens</th><th className="text-right">secs</th>
              </tr>
            </thead>
            <tbody>
              {measure.summary.rows.map((r, i) => (
                <tr key={i} className="border-t border-slate-100">
                  <td className="pr-4">{r.stage}</td>
                  <td className="pr-4">{r.provider}{r.is_local ? ' (local)' : ''}</td>
                  <td className="text-right pr-4">{r.approx_total_tokens.toLocaleString()}</td>
                  <td className="text-right">{r.secs}s</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="mt-1 text-slate-400">
            Heaviest: {measure.summary.heaviest_by_tokens.stage} by tokens, {measure.summary.heaviest_by_secs.stage} by time.
            Local gens are skipped here (they load a multi-GB model) — measure them in a memory-safe window via the CLI.
          </p>
        </div>
      )}

      {error && <Banner kind="error">{error}</Banner>}
    </SectionCard>
  );
}
