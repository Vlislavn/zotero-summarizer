import { trackTodayLink } from '../../../utils/todayEvents.js';

// The paper's code repository, extracted + validated during deep review
// (services.library._code_link). ONE quiet inline row — "Code · repo · status" —
// not a coloured block: liveness is a plain note (a dead link turns ochre), and
// whether the repo plausibly belongs to THIS paper vs a cited dependency is the
// note's wording. Older cached reviews have no code_link → the caller renders nothing.
export default function CodeLink({ codeLink }) {
  const cl = codeLink || {};
  if (!cl.found) return <div className="text-[13px] text-slate-500">No code repository linked in the paper.</div>;
  const label = String(cl.url || '').replace(/^https?:\/\//, '');
  const live = cl.exists === true;
  const dead = cl.exists === false;
  let note;
  if (dead) note = `not reachable${cl.status ? ` (${cl.status})` : ''} — may be renamed or private`;
  else if (live && cl.relevance === 'matched') note = '✓ live · matches the paper';
  else if (live) note = '✓ live · confirm it’s this paper’s repo';
  else note = cl.checked === false ? 'not validated (offline)' : 'could not validate';
  return (
    <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 text-[13px] leading-snug text-slate-600">
      <span className="text-[11px] font-medium uppercase tracking-[0.08em] text-slate-500">Code</span>
      <a href={cl.url} target="_blank" rel="noopener noreferrer" onClick={() => trackTodayLink('code')} onAuxClick={() => trackTodayLink('code')} className="break-all font-medium text-forest-800 hover:underline">{label}</a>
      <span className={dead ? 'text-amber-800' : 'text-slate-500'}>· {note}</span>
    </div>
  );
}
