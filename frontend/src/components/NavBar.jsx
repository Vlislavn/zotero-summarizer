import { useEffect, useRef, useState } from 'react';
import { NavLink, useLocation } from 'react-router-dom';
import OfflineStatus from './OfflineStatus.jsx';

// Today is the landing page (`/` redirects there); Weekly opens retained research snapshots.
const PRIMARY = [
  { to: '/today', label: 'Today' },
  { to: '/library', label: 'Library' },
  { to: '/search', label: 'Search' },
  { to: '/weekly', label: 'Weekly' },
  { to: '/conferences', label: 'Conferences' },
];
// Below 640 px these two live behind the “⋯” disclosure so the phone nav stays one row.
const MORE = [
  { to: '/settings', label: 'Settings' },
  { to: '/ops', label: 'Ops' },
];

const BASE = 'min-h-11 inline-flex items-center min-w-11 px-1 sm:px-3 rounded-lg text-sm font-medium transition-colors';
const idle = 'text-slate-700 hover:bg-slate-200';
const current = 'bg-forest-800 text-white';
const tabClass = ({ isActive }) => `${BASE} justify-center ${isActive ? current : idle}`;
const moreLinkClass = ({ isActive }) => `${BASE} px-3 ${isActive ? current : idle}`;

// On ≥640 px `sm:contents` dissolves the wrapper so Settings/Ops sit inline in the row.
function MoreMenu() {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef(null);
  const buttonRef = useRef(null);
  const { pathname } = useLocation();
  const onMorePage = MORE.some((m) => pathname === m.to || pathname.startsWith(`${m.to}/`));
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (event) => {
      if (event.key !== 'Escape') return;
      setOpen(false);
      buttonRef.current?.focus();
    };
    const onPointer = (event) => { if (!wrapRef.current?.contains(event.target)) setOpen(false); };
    document.addEventListener('keydown', onKey);
    document.addEventListener('pointerdown', onPointer);
    return () => {
      document.removeEventListener('keydown', onKey);
      document.removeEventListener('pointerdown', onPointer);
    };
  }, [open]);
  return (
    <div
      ref={wrapRef}
      className="relative sm:contents"
      onBlur={(event) => { if (open && !wrapRef.current?.contains(event.relatedTarget)) setOpen(false); }}
    >
      <button
        ref={buttonRef}
        type="button"
        aria-label="More: Settings, Ops"
        aria-expanded={open}
        aria-controls="nav-more"
        onClick={() => setOpen(!open)}
        className={`${BASE} justify-center sm:hidden ${onMorePage ? current : idle}`}
      >
        <span aria-hidden="true">⋯</span>
      </button>
      <div
        id="nav-more"
        className={`sm:contents ${open
          ? 'absolute right-0 top-full mt-1 z-40 flex min-w-36 flex-col gap-1 rounded-xl border border-slate-200 bg-white p-1 shadow-lg'
          : 'hidden'}`}
      >
        {MORE.map((t) => (
          <NavLink key={t.to} to={t.to} className={moreLinkClass} onClick={() => setOpen(false)}>
            {t.label}
          </NavLink>
        ))}
      </div>
    </div>
  );
}

export default function NavBar() {
  return (
    <header className="glass sm:border border-slate-200 rounded-2xl px-1 sm:p-4 mb-2 sm:mb-5 overflow-visible relative z-30">
      <div className="flex items-center gap-x-4 flex-wrap">
        {/* Not a heading: each page owns the single <h1> (PageTitle). */}
        <p className="sr-only sm:not-sr-only font-display text-2xl font-light text-slate-900">Zotero Summarizer</p>
        <nav className="flex max-w-full gap-0.5 sm:gap-1 sm:flex-wrap items-center">
          {PRIMARY.map((t) => (
            <NavLink key={t.to} to={t.to} className={tabClass}>
              {t.label}
            </NavLink>
          ))}
          <MoreMenu />
        </nav>
      </div>
      <OfflineStatus />
    </header>
  );
}
