import { NavLink } from 'react-router-dom';
import OfflineStatus from './OfflineStatus.jsx';

// Today is the landing page (`/` redirects there); Weekly opens retained research snapshots.
const PRIMARY = [
  { to: '/today', label: 'Today' },
  { to: '/library', label: 'Library' },
  { to: '/search', label: 'Search' },
  { to: '/weekly', label: 'Weekly' },
  { to: '/settings', label: 'Settings' },
  { to: '/ops', label: 'Ops' },
];

function tabClass({ isActive }) {
  return [
    'min-h-11 inline-flex items-center justify-center min-w-11 px-1.5 sm:px-3 rounded-lg text-sm font-medium transition-colors',
    isActive
      ? 'bg-forest-800 text-white'
      : 'text-slate-700 hover:bg-slate-200',
  ].join(' ');
}

export default function NavBar() {
  return (
    <header className="glass sm:border border-slate-200 rounded-2xl px-1 sm:p-4 mb-2 sm:mb-5 overflow-visible relative z-30">
      <div className="flex items-center gap-x-4 flex-wrap">
        <h1 className="sr-only sm:not-sr-only font-display text-2xl font-light text-slate-900">Zotero Summarizer</h1>
        <nav className="flex flex-wrap max-w-full gap-1 items-center">
          {PRIMARY.map((t) => (
            <NavLink key={t.to} to={t.to} className={tabClass}>
              {t.label}
            </NavLink>
          ))}
        </nav>
      </div>
      <OfflineStatus />
    </header>
  );
}
