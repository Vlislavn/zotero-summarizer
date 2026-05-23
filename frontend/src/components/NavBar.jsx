import { NavLink, useLocation } from 'react-router-dom';

// Primary tabs: Today / Annotate / Settings. Everything else hides
// behind a "More" disclosure (Hick's Law — the daily flow stays at 3
// choices). When the user is on a power-tool route the bar tags it
// with a breadcrumb so they don't feel lost.
const PRIMARY = [
  { to: '/today', label: 'Today' },
  { to: '/annotate', label: 'Annotate' },
  { to: '/settings', label: 'Settings' },
];

const POWER_TOOLS = [
  { to: '/library', label: 'Library' },
  { to: '/triage', label: 'Triage' },
  { to: '/review', label: 'Feed Review' },
  { to: '/pending', label: 'Pending' },
  { to: '/audit', label: 'Re-label Audit' },
];

function tabClass({ isActive }) {
  return [
    'px-3 py-1.5 rounded-lg text-sm font-medium transition-colors',
    isActive
      ? 'bg-slate-900 text-white'
      : 'text-slate-700 hover:bg-slate-200',
  ].join(' ');
}

function powerLinkClass({ isActive }) {
  return [
    'px-3 py-1.5 rounded-lg text-sm font-medium transition-colors text-left',
    isActive
      ? 'bg-slate-100 text-slate-900 font-semibold'
      : 'text-slate-700 hover:bg-slate-100',
  ].join(' ');
}

export default function NavBar() {
  const { pathname } = useLocation();
  const activePower = POWER_TOOLS.find((t) => pathname.startsWith(t.to));

  return (
    <header className="glass border border-slate-200 rounded-2xl shadow-lg p-4 mb-5 overflow-visible relative z-30">
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <div className="flex items-center gap-4 flex-wrap">
          <h1 className="text-xl font-bold text-slate-900">Zotero Summarizer</h1>
          <nav className="flex gap-1.5 items-center">
            {PRIMARY.map((t) => (
              <NavLink key={t.to} to={t.to} className={tabClass}>
                {t.label}
              </NavLink>
            ))}
            {activePower && (
              <>
                <span className="text-slate-300 px-1" aria-hidden>·</span>
                <NavLink to={activePower.to} className={tabClass}>
                  {activePower.label}
                </NavLink>
              </>
            )}
          </nav>
        </div>

        {/* The "More" disclosure uses an explicit chevron + hover style
            so the affordance reads as a menu, not as static text. The
            absolute panel positions itself relative to the header card. */}
        <details className="text-sm relative group">
          <summary
            className="cursor-pointer select-none list-none px-3 py-1.5 rounded-lg
                       border border-slate-200 bg-white text-slate-700
                       hover:bg-slate-100 hover:border-slate-300
                       group-open:bg-slate-100 group-open:border-slate-300
                       inline-flex items-center gap-1.5"
          >
            <span className="text-[15px] leading-none" aria-hidden>⋯</span>
            <span>More</span>
            <span className="text-slate-400 text-xs group-open:rotate-180 transition-transform" aria-hidden>▾</span>
          </summary>
          <div className="absolute right-0 mt-2 z-20 bg-white border border-slate-200 rounded-xl shadow-lg p-2 flex flex-col gap-1 min-w-[200px]">
            <div className="px-3 py-1 text-[10px] uppercase tracking-wider text-slate-400">
              Power tools
            </div>
            {POWER_TOOLS.map((t) => (
              <NavLink key={t.to} to={t.to} className={powerLinkClass}>
                {t.label}
              </NavLink>
            ))}
          </div>
        </details>
      </div>
    </header>
  );
}
