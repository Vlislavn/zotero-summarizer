import { METHOD_CLAUSE, LEGEND, decisiveRows, fullChecklist } from './briefModel.js';

const RB_TONE = {
  yes: 'bg-emerald-100 text-emerald-700',
  no: 'bg-rose-100 text-rose-700',
  na: 'bg-slate-100 text-slate-400',
};
function RubricMark({ value }) {
  return (
    <span className={`shrink-0 inline-block min-w-[2.2rem] text-center rounded text-[10px] font-medium uppercase px-1 py-0.5 ${RB_TONE[value] || RB_TONE.na}`}>
      {value}
    </span>
  );
}

// The EXPLANATION half, inside the "Details" disclosure: how the grade was reached
// (method clause), the decisive signals, overstated claims, the full rubric, and
// the legend. Flattened — no nested disclosure (we are already inside one).
export function QualityDetails({ quality, band, hideMaterial = false }) {
  const overs = (quality.overstatements || []).map((x) => String(x || '').trim()).filter(Boolean);
  const agreed = Number(quality.passes_agreed) || 0;
  const total = Number(quality.passes_total) || 0;
  const { heading, rows, caption } = decisiveRows(quality.rubric || {}, band);
  const checklist = fullChecklist(quality.rubric || {}, quality.evidence || {});

  return (
    <div className="space-y-2.5">
      <p className="text-[11px] leading-relaxed text-slate-400 max-w-[66ch]">
        {METHOD_CLAUSE}{total > 1 ? ` · ${agreed}/${total} passes agree` : total === 1 ? ' · 1 pass (no agreement check)' : ''}
      </p>

      {rows.length > 0 && (
        <div>
          <div className="text-[11px] uppercase tracking-[0.06em] font-medium text-slate-400 mb-1">{heading}</div>
          <ul className="space-y-0.5 text-[13px] leading-relaxed">
            {rows.map((r, i) => (
              <li key={i} className="flex items-baseline gap-1.5">
                <span className={`font-medium ${r.ok ? 'text-emerald-600' : 'text-rose-600'}`}>{r.ok ? '✓' : '✗'}</span>
                <span className="text-slate-700">{r.label}</span>
              </li>
            ))}
          </ul>
          {caption && <div className="mt-1 text-[11px] text-slate-400">{caption}</div>}
        </div>
      )}

      {!hideMaterial && overs.length > 0 && (
        <div>
          <div className="mb-1 text-[11px] uppercase tracking-[0.06em] font-medium text-amber-600">
            Possible overstatement (low reliability)
          </div>
          <ul className="list-disc pl-5 text-[13px] leading-relaxed text-slate-700 space-y-0.5">
            {overs.map((x, i) => <li key={i}>{x}</li>)}
          </ul>
        </div>
      )}

      {checklist.length > 0 && (
        <div>
          <div className="text-[11px] uppercase tracking-[0.06em] font-medium text-slate-400 mb-1">
            Full {checklist.length}-point checklist
          </div>
          <div className="space-y-1">
            {checklist.map((r) => (
              <div key={r.key} className="flex items-baseline gap-2 text-[12px] leading-relaxed">
                <RubricMark value={r.value} />
                <span className="text-slate-700">
                  {r.label}
                  {r.quote && <span className="block text-[11px] italic text-slate-400 mt-0.5">“{r.quote}”</span>}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="pt-2 border-t border-slate-200/60 text-[11px] leading-relaxed text-slate-400 max-w-[66ch]">
        {LEGEND.map((l, i) => (
          <span key={l.term}>
            {i > 0 && ' · '}
            <b className="font-medium text-slate-500">{l.term}</b> {l.gloss}
          </span>
        ))}
      </div>
    </div>
  );
}
