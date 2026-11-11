const LABEL = 'text-[11px] font-medium uppercase tracking-[0.08em] text-slate-500';

// The ordered reading plan inside the verdict card: "Read first" targets, then
// quiet "Skip" and "What the original adds" rows (label left on sm+, stacked on phones).
export default function ReadingPlan({ digest, decision }) {
  const opensOriginal = decision !== 'skip';
  const targets = (digest.read_parts || []).filter(part => part.trim());
  const skips = (digest.skip_parts || []).filter(part => part.trim());
  if (!opensOriginal && !skips.length) return null;

  const row = 'grid grid-cols-1 gap-x-3 gap-y-0.5 sm:grid-cols-[8rem_minmax(0,1fr)]';
  return (
    <div role="region" aria-label="Reading plan" className="mt-3 min-w-0 space-y-2.5 break-words border-t border-slate-200/70 pt-3 [&_dt]:text-slate-600 [&_li]:!text-[15px] [&_p]:!text-[15px]">
      {opensOriginal && <div className={row}>
        <div className={`${LABEL} pt-1`}>Read first</div>
        {targets.length ? <ol aria-label="Read parts" className="list-decimal space-y-0.5 pl-5 marker:text-slate-400">
          {targets.map((target, i) => <li key={i}>{target}</li>)}
        </ol> : <p>Exact reading targets not recorded in this saved review.</p>}
      </div>}
      {skips.length > 0 && <div className={row}>
        <div className={`${LABEL} pt-1`}>Skip</div>
        <ul className="flex flex-wrap gap-x-4 gap-y-0.5 text-slate-600">
          {skips.map((part, i) => <li key={i}>{part}</li>)}
        </ul>
      </div>}
      {opensOriginal && <div className={row}>
        <div className={`${LABEL} pt-1`}>What the original adds</div>
        <p>{digest.original_value || 'Additional value not recorded in this saved review.'}</p>
      </div>}
    </div>
  );
}
