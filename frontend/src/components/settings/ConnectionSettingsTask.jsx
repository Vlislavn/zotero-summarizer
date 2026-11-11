import { useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { fetchConfig } from '../../api/settingsApi.js';
import { taskReturnTo, returnLabel } from '../../utils/settingsNavigation.js';
import ConnectionTask from './ConnectionTask.jsx';
import TaskPage from './TaskPage.jsx';

export default function ConnectionSettingsTask() {
  const location = useLocation();
  const cache = useQueryClient();
  const destination = taskReturnTo(location);
  const [activity, setActivity] = useState({ dirty: false, pending: false });
  const query = useQuery({ queryKey: ['runtime-config'], queryFn: fetchConfig, retry: false });
  return <TaskPage title="AI models" backTo={destination} backLabel={returnLabel(destination)}
    description="Choose which model your work uses. Saving and running a model check are separate actions."
    dirty={activity.dirty} pending={activity.pending} leaveMessage="Leave this connection task? Unsaved choices will be discarded. Registered servers and separately stored credentials remain saved; leaving does not cancel sent work.">
    {query.data?.llm_enabled === false && <p role="status" className="rounded-xl border border-slate-200 p-4 text-sm">
      AI-assisted features are off. You can still configure a connection without running a model.{' '}
      <Link to="/settings?task=mode" className="underline text-forest-800">Change AI preference</Link>
    </p>}
    <div id="ai-models" className="rounded-2xl border border-slate-200 p-4 min-w-0">
      <ConnectionTask autoOpen={query.isError || query.isSuccess && !query.data?.llm_routing?.default} onActivityChange={setActivity} onSaved={(config, { previousRevision } = {}) => {
        const known = cache.getQueryData(['runtime-config']);
        if (!known || known._revision === previousRevision || known._revision === config._revision) cache.setQueryData(['runtime-config'], config);
        else {
          cache.invalidateQueries({ queryKey: ['runtime-config'] });
          cache.invalidateQueries({ queryKey: ['llm-connections'] });
        }
        cache.invalidateQueries({ queryKey: ['setup-status'] });
      }} />
    </div>
  </TaskPage>;
}
