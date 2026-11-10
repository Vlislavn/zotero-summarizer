import { useEffect, useRef } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { fetchConfig } from '../../api/settingsApi.js';
import { useSetupStatus } from '../../hooks/useSetupStatus.js';
import { taskUrl } from '../../utils/settingsNavigation.js';
import { humanizeError } from '../../utils/humanizeError.js';
import ReadinessStrip from './ReadinessStrip.jsx';
import { REVIEW_DEPTHS } from '../../utils/reviewDepth.js';
import { CHECKLIST_ENGINES } from '../../utils/checklistEngine.js';
import StepModelsTable from './StepModelsTable.jsx';
import Button from '../ui/Button.jsx';
import { WeeklyAnalysisRow } from '../FeedbackAnalysisCard.jsx';

// `set` only says the env var is present; `value` is the resolved path the app actually uses.
function zoteroPathText(entry) {
  if (!entry?.value) return 'Path status unknown';
  const origin = entry.set ? 'configured' : 'default location';
  return `${entry.value} · ${origin}${entry.exists ? '' : ' · folder not found'}`;
}

// The visible action is the same short word on every row; `action` stays the link's full name.
function PreferenceRow({ title, value, to, action, task }) {
  return <div className="flex flex-wrap items-center justify-between gap-3 py-5 border-t border-slate-200 first:border-t-0">
    <div className="min-w-0 flex-1 basis-48"><h3 className="text-base font-medium text-slate-900">{title}</h3>
      <p className="mt-1 text-sm text-slate-600 [overflow-wrap:anywhere]">{value}</p></div>
    <Link id={`task-${task}`} to={to} className="inline-flex min-h-11 items-center rounded-lg border border-slate-300 px-4 py-2 text-sm text-forest-800 hover:bg-linen focus-visible:ring-2 focus-visible:ring-forest-800" aria-label={action}>Change</Link>
  </div>;
}

export default function SettingsOverview() {
  const location = useLocation();
  const title = useRef(null);
  const query = useQuery({ queryKey: ['runtime-config'], queryFn: fetchConfig, retry: false });
  const { status } = useSetupStatus();
  useEffect(() => {
    if (query.isPending) return;
    const trigger = location.state?.focusTask && document.getElementById(`task-${location.state.focusTask}`);
    (trigger || title.current)?.focus();
  }, [query.isPending, location.state?.focusTask]);
  const config = query.data;
  const routing = config?.llm_routing;
  return <div className="max-w-4xl mx-auto space-y-7 pb-8">
    <header className="space-y-2"><h1 ref={title} tabIndex={-1} className="font-display text-3xl font-light text-forest-800 focus:outline-none">Settings</h1>
      <p className="text-sm text-slate-600">Which model runs each step, plus your preferences.</p></header>
    <ReadinessStrip />
    {query.isPending && <p role="status">Loading saved preferences…</p>}
    {query.isError && <section role="alert" className="rounded-xl border border-rose-200 bg-rose-50 p-4 space-y-3">
      <p>Saved configuration is unavailable. {humanizeError(query.error)}</p>
      <Link className="underline text-forest-800" to="/settings?task=connection">Repair or establish an AI connection</Link>
      <Button type="button" variant="secondary" onClick={() => query.refetch()}>Retry loading settings</Button>
      <p className="text-sm">Existing invalid configuration is not overwritten. Source management remains available in Today and Weekly.</p>
    </section>}
    {config && <>
      <section aria-labelledby="settings-processing" className="rounded-2xl border border-slate-200 bg-white p-5">
        <h2 id="settings-processing" className="font-display text-xl font-light text-forest-800">Processing</h2>
        <PreferenceRow title="AI-assisted features" value={config.llm_enabled === false ? 'Off · ML feed ranking remains available' : 'On · reviews, summaries and Ask Paper'} task="mode" to={taskUrl(location, 'mode')} action="Change AI mode" />
        <div className="py-5 border-t border-slate-200 space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h3 className="text-base font-medium text-slate-900">Model for each step</h3>
            <Link id="task-connection" to={taskUrl(location, 'connection')} className="inline-flex min-h-11 items-center rounded-lg border border-slate-300 px-4 py-2 text-sm text-forest-800 hover:bg-linen focus-visible:ring-2 focus-visible:ring-forest-800">Choose models</Link>
          </div>
          {routing?.default?.model ? <StepModelsTable routing={routing} /> : <p className="text-sm text-slate-600">No model saved yet.</p>}
        </div>
        <PreferenceRow title="Review depth" value={REVIEW_DEPTHS[config.review_depth === 'fast' ? 'fast' : 'thorough'].summary} task="depth" to={taskUrl(location, 'depth')} action="Change review depth" />
        <PreferenceRow title="Checklist engine" value={CHECKLIST_ENGINES[['local', 'cloud'].includes(config.checklist_engine) ? config.checklist_engine : 'off'].summary} task="engine" to={taskUrl(location, 'engine')} action="Change checklist engine" />
        <PreferenceRow title="Background processing" value={`${config.quality_review?.fleet_papers ?? 8} papers at once · ${config.quality_review?.calls_per_paper ?? 2} calls each · feeds read back ${config.feeds?.backfill_days ?? 7} days, ${config.feeds?.max_pages_per_feed ?? 10} pages`} task="processing" to={taskUrl(location, 'processing')} action="Change background processing" />
        <WeeklyAnalysisRow />
      </section>
      <section aria-labelledby="settings-connections" className="rounded-2xl border border-slate-200 bg-white p-5">
        <h2 id="settings-connections" className="font-display text-xl font-light text-forest-800">Zotero &amp; access</h2>
        <PreferenceRow title="Zotero & PDFs" value={zoteroPathText(status?.paths?.zotero_data_dir)} task="zotero" to={taskUrl(location, 'zotero')} action="Change Zotero paths" />
        <PreferenceRow title="Library access" value={config.university_access?.enabled ? 'Browser fetch enabled · sign-in is separate' : 'Browser fetch off · open-access retrieval is unchanged'} task="university" to={taskUrl(location, 'university')} action="Change library access" />
      </section>
      <section aria-labelledby="settings-output" className="rounded-2xl border border-slate-200 bg-white p-5">
        <h2 id="settings-output" className="font-display text-xl font-light text-forest-800">Output</h2>
        <PreferenceRow title="Output language" value={config.output_language || 'Not set'} task="language" to={taskUrl(location, 'language')} action="Change output language" />
      </section>
    </>}
    <nav aria-label="Task settings in your workspace" className="space-y-3 text-sm [&_a]:inline-flex [&_a]:items-center [&_a]:min-h-11">
      <h2 className="font-display text-xl font-light text-forest-800">Looking for task settings?</h2>
      <ul className="grid gap-3 sm:grid-cols-2 text-forest-800">
        <li><Link className="underline underline-offset-4" to="/today?task=selection">Article selection → Today</Link></li>
        <li><Link className="underline underline-offset-4" to="/today?task=rss">RSS sources → Today</Link></li>
        <li><Link className="underline underline-offset-4" to="/weekly?task=telegram">Telegram sources → Weekly</Link></li>
        <li><Link className="underline underline-offset-4" to="/ops?tab=system">Readiness & calibration → Ops</Link></li>
      </ul>
    </nav>
  </div>;
}
