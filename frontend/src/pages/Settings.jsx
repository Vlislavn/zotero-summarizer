import { Link, Navigate, useLocation, useSearchParams } from 'react-router-dom';
import { legacySettingsDestination } from '../utils/settingsNavigation.js';
import SettingsOverview from '../components/settings/SettingsOverview.jsx';
import ConfigTask from '../components/settings/ConfigTask.jsx';
import ConnectionSettingsTask from '../components/settings/ConnectionSettingsTask.jsx';
import ZoteroPathsTask from '../components/settings/ZoteroPathsTask.jsx';

export default function Settings() {
  const location = useLocation();
  const [params] = useSearchParams();
  const legacy = legacySettingsDestination(location);
  if (legacy) return <Navigate replace to={legacy} state={location.state} />;
  const task = params.get('task');
  if (!task) return <SettingsOverview />;
  if (task === 'connection') return <ConnectionSettingsTask key={task} />;
  if (task === 'zotero') return <ZoteroPathsTask key={task} />;
  if (['mode', 'language', 'university', 'depth', 'engine', 'processing'].includes(task)) return <ConfigTask key={task} task={task} />;
  return <section role="alert" className="space-y-4"><h1 className="font-display text-2xl font-light">Settings task not found</h1>
    <p>No configuration was changed.</p><Link to="/settings" className="underline text-forest-800">Back to Settings</Link></section>;
}
