import { Link, useLocation } from 'react-router-dom';

export default function SettingsTaskLink({ task, children, className }) {
  const location = useLocation();
  return <Link to={`/settings?task=${encodeURIComponent(task)}`} className={className}
    state={{ returnTo: location.pathname + location.search + location.hash }}>{children}</Link>;
}
