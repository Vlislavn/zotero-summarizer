import { useEffect, useState } from 'react';
import { Link, useLocation, useNavigate, useSearchParams } from 'react-router-dom';
import DeploymentCard from '../components/settings/DeploymentCard.jsx';
import CalibrationCard from '../components/settings/CalibrationCard.jsx';
import TaskPage from '../components/settings/TaskPage.jsx';
import { taskUrl } from '../utils/settingsNavigation.js';
import Review from './Review.jsx';
import Triage from './Triage.jsx';
import Pending from './Pending.jsx';
import AdminSection from '../components/AdminSection.jsx';
import ModelCard from '../components/ModelCard.jsx';
import FeedbackAnalysisCard from '../components/FeedbackAnalysisCard.jsx';

function SystemOps() {
  const location = useLocation();
  const task = new URLSearchParams(location.search).get('task');
  useEffect(() => {
    if (!task && location.state?.focusTask) document.getElementById(`task-${location.state.focusTask}`)?.focus();
  }, [task, location.state?.focusTask]);
  if (task === 'readiness' || task === 'calibration') return <TaskPage
    title={task === 'readiness' ? 'Check readiness' : 'Calibrate reviews'} backTo={taskUrl(location, null)} backLabel="Back to System"
    description="Work starts only when explicitly requested. Leaving stops viewing, not server work; return to read the current status.">
    {task === 'readiness' ? <DeploymentCard /> : <CalibrationCard />}
  </TaskPage>;
  return <div className="space-y-5"><h2 className="font-display text-3xl font-light text-forest-800">System</h2>
    <nav aria-label="System maintenance" className="flex flex-wrap gap-4 text-sm text-forest-800">
      <Link id="task-readiness" className="min-h-11 inline-flex items-center underline" to={taskUrl(location, 'readiness')}>Check readiness</Link>
      <Link id="task-calibration" className="min-h-11 inline-flex items-center underline" to={taskUrl(location, 'calibration')}>Calibrate reviews</Link>
    </nav><FeedbackAnalysisCard /><ModelCard /><AdminSection /></div>;
}

// Operator-only workflows share one tabbed surface; legacy deep links still work.

const TABS = [
  { id: 'review', label: 'Feed review', Body: Review },
  { id: 'triage', label: 'Triage jobs', Body: Triage },
  { id: 'pending', label: 'Pending changes', Body: Pending },
  { id: 'system', label: 'System', Body: SystemOps },
];

const VALID_TABS = new Set(TABS.map((t) => t.id));
const DEFAULT_TAB = 'review';

function readInitialTab(searchParams, hash) {
  const fromQuery = searchParams.get('tab');
  if (fromQuery && VALID_TABS.has(fromQuery)) return fromQuery;
  const fromHash = (hash || '').replace(/^#/, '');
  if (fromHash && VALID_TABS.has(fromHash)) return fromHash;
  if (['readiness', 'calibration'].includes(searchParams.get('task'))) return 'system';
  return DEFAULT_TAB;
}

export default function Ops() {
  const [searchParams] = useSearchParams();
  const { hash } = useLocation();
  const navigate = useNavigate();
  const [tab, setTab] = useState(() => readInitialTab(searchParams, hash));

  useEffect(() => {
    setTab(readInitialTab(searchParams, hash));
  }, [searchParams, hash]);

  function selectTab(id) {
    setTab(id);
    const next = new URLSearchParams(searchParams);
    next.set('tab', id);
    if (id !== tab) next.delete('task');
    navigate({ search: `?${next.toString()}` }, { replace: true });
  }

  const ActiveBody = (TABS.find((t) => t.id === tab) ?? TABS[0]).Body;

  return (
    <div>
      <div
        role="tablist"
        aria-label="Ops sections"
        className="flex flex-wrap gap-1.5 mb-4"
      >
        {TABS.map((t) => {
          const active = t.id === tab;
          return (
            <button
              key={t.id}
              type="button"
              role="tab"
              aria-selected={active}
              onClick={() => selectTab(t.id)}
              className={[
                'px-3 py-1.5 rounded-lg text-sm font-medium transition-colors border',
                active
                  ? 'bg-forest-800 text-white border-forest-800'
                  : 'bg-white text-slate-700 border-slate-200 hover:bg-slate-100',
              ].join(' ')}
            >
              {t.label}
            </button>
          );
        })}
      </div>
      <ActiveBody />
    </div>
  );
}
