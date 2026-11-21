import { Link, useLocation } from 'react-router-dom';
import Spinner from '../ui/Spinner.jsx';
import { taskUrl } from '../../utils/settingsNavigation.js';

const menuLink = 'inline-flex min-h-11 items-center text-sm text-forest-800 underline underline-offset-4 sm:min-h-9';
const secondary = 'inline-flex min-h-11 items-center gap-1.5 text-sm text-forest-800 underline underline-offset-4 disabled:no-underline disabled:opacity-50 sm:min-h-9';

// The collapsible «More» row: refresh, optional backlog triage and the settings links.
export default function TodayMoreMenu({ show, showRefresh, view, triage }) {
  const location = useLocation();
  return <div id="today-more" className={`${show ? 'mt-2 flex' : 'hidden'} flex-wrap items-center gap-x-5 gap-y-0.5 rounded-lg bg-slate-50 px-3 py-1`}>
    {showRefresh && <button type="button" onClick={view.refresh}
      disabled={view.busy} className={secondary}>Refresh status</button>}
    <button type="button" onClick={() => triage.mutation.mutate()} disabled={triage.draining || triage.unknown}
      title="Optional: rank the un-triaged feed backlog by relevance. This does not replace this shortlist. Processed items are marked read."
      className={secondary}>
      {triage.draining && !triage.unknown && <Spinner size="xs" color="teal" />}{triage.label}
    </button>
    <nav aria-label="Today task settings" className="contents">
      <Link id="task-selection" className={menuLink} to={taskUrl(location, 'selection')}>Article selection</Link>
      <Link id="task-rss" className={menuLink} to={taskUrl(location, 'rss')}>RSS sources</Link>
      <Link className={menuLink} to="/weekly">Telegram today</Link>
    </nav>
  </div>;
}
