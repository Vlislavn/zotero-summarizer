import { Fold, Chip } from './primitives.jsx';

// The paper's structure as a SECONDARY, collapsed affordance — "touch the paper
// without reading every word": each section's title + page + the one-line "what it
// covers" (the grounded summary, built when the paper is opened — `summaries` is the
// job's { status, summaries }; its line is reserved while it runs so rows never jump) + a quiet
// finding indicator. NOT the spine — the findings live up in the review; this is
// the map they anchor to. Each row's id `secmap-<id>` is the scroll target the
// review's located-finding chips link to. Suppressed when the overlay is degraded
// (page-sentinel / docling sections), where anchoring would mislabel.
export default function SectionMap({ overlay, summaries }) {
  const sections = overlay?.sections || [];
  if (!sections.length || overlay?.degraded) return null;

  const flagged = new Set();
  const matched = new Set();
  const lines = summaries?.summaries || {};
  const pending = summaries?.status === 'running';
  // Only assert a section ⚑ for a CONFIDENT location (exact/fuzzy span) — an
  // approximate fallback is too uncertain to mark a whole section as problematic.
  for (const r of overlay.red_flags || []) if (r.section && r.match !== 'approx') flagged.add(r.section.id);
  for (const m of overlay.missing_critical || []) if (m.section && m.match !== 'approx') flagged.add(m.section.id);
  for (const g of overlay.goals || []) for (const s of g.sections || []) matched.add(s.id);

  return (
    <Fold id="paper-map" label="Paper map" summary={`${sections.length} sections, with where each finding sits`}>
      <ol className="divide-y divide-slate-200/60">
        {sections.map((s) => (
          <li key={s.id} id={`secmap-${s.id}`} className="scroll-mt-24 flex items-baseline gap-3 py-2">
            <span className="w-9 shrink-0 text-[11px] tabular-nums text-slate-400">
              {s.page ? `p.${s.page}` : '—'}
            </span>
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-[13px] font-medium text-slate-800">{s.title}</span>
                {flagged.has(s.id) && <Chip tone="rose" title="The review flagged a problem in this section">⚑ Flagged</Chip>}
                {!flagged.has(s.id) && matched.has(s.id) && <Chip tone="emerald" title="Addresses one of your goals">◎ Goal</Chip>}
              </div>
              {(lines[s.id] || s.summary)
                ? <p className="mt-0.5 max-w-[66ch] text-[12px] leading-relaxed text-slate-500">{lines[s.id] || s.summary}</p>
                : pending && <div className="mt-0.5 h-[1.2rem]" aria-hidden="true" />}
            </div>
          </li>
        ))}
      </ol>
    </Fold>
  );
}
