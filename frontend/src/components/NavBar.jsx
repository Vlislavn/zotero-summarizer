import { NavLink } from 'react-router-dom';
import OfflineStatus from './OfflineStatus.jsx';

// Library remains the landing tab; Weekly browses retained research digests.
const PRIMARY = [
  { to: '/library', label: 'Library' },
  { to: '/search', label: 'Search' },
  { to: '/today', label: 'Today' },
  { to: '/weekly', label: 'Weekly' },
  { to: '/settings', label: 'Settings' },
  { to: '/ops', label: 'Ops' },
];

function tabClass({ isActive }) {
  return [
    'px-3 py-1.5 rounded-lg text-sm font-medium transition-colors',
    isActive
      ? 'bg-forest-800 text-white'
      : 'text-slate-700 hover:bg-slate-200',
  ].join(' ');
}

export default function NavBar() {
  return (
    <header className="glass border border-slate-200 rounded-2xl p-4 mb-5 overflow-visible relative z-30">
      <div className="flex items-center gap-4 flex-wrap">
        <h1 className="font-display text-2xl font-light text-slate-900">Zotero Summarizer</h1>
        <nav className="flex flex-wrap max-w-full gap-1.5 items-center">
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
