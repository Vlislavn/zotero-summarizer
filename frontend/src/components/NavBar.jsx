import { NavLink } from 'react-router-dom';

// Primary tabs: Library / Today / Settings / Ops (Increment 3 nav collapse —
// 8 routes folded to 3 daily surfaces + one Ops surface). Library (Read next) is
// the landing surface and the leftmost "home" tab (Serial Position / Jakob's
// Law: the default view IS the first tab), since it carries both daily workflows
// (Read-next queue + Meaning search) AND the folded-in Batch-label mode (the
// former Annotate page). Today (feed cull) sits next, then Settings. Ops is the
// rarely-used operator surface (Feed review + Triage jobs + Pending changes) on
// its own tab — Hick's Law: the bar stays at four flat choices, no disclosure.
const PRIMARY = [
  { to: '/library', label: 'Library' },
  { to: '/today', label: 'Today' },
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
        <nav className="flex gap-1.5 items-center">
          {PRIMARY.map((t) => (
            <NavLink key={t.to} to={t.to} className={tabClass}>
              {t.label}
            </NavLink>
          ))}
        </nav>
      </div>
    </header>
  );
}
