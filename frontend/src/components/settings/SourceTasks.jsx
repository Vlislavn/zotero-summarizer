import { useState } from 'react';
import { useLocation } from 'react-router-dom';
import { returnLabel, taskReturnTo } from '../../utils/settingsNavigation.js';
import TaskPage from './TaskPage.jsx';
import RssFeedsSection from './RssFeedsSection.jsx';
import TelegramSources from './TelegramSources.jsx';

export function RssSourcesTask() {
  const location = useLocation();
  const backTo = taskReturnTo(location);
  const [dirty, setDirty] = useState(false);
  const [pending, setPending] = useState(false);
  return <TaskPage title="RSS sources" backTo={backTo} backLabel={returnLabel(backTo)} dirty={dirty} pending={pending}
    description="Manage subscriptions for Today. Saving, fetching RSS and triaging the backlog are separate actions.">
    <RssFeedsSection onDirtyChange={setDirty} onPendingChange={setPending} />
  </TaskPage>;
}

export function TelegramSourcesTask() {
  const location = useLocation();
  const backTo = taskReturnTo(location);
  const [dirty, setDirty] = useState(false);
  const [pending, setPending] = useState(false);
  return <TaskPage title="Telegram sources" backTo={backTo} backLabel={returnLabel(backTo)} dirty={dirty} pending={pending}
    description="Choose the public channels and where summaries are made, then allow reading them.">
    <TelegramSources onDirtyChange={setDirty} onPendingChange={setPending} />
  </TaskPage>;
}
