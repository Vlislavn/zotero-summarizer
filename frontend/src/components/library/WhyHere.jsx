import { SIGNAL_LABEL, explainRow } from '../../utils/readNextOrder.js';
import { GROUP_LINE } from './readNextCopy.js';
import { scoreToBand } from '../../utils/relevanceBands.js';

const BAND_RANGE = { must_read: '4.5–5', should_read: '3.5–4.5', could_read: '2.0–3.5', dont_read: '0–2.0' };
const UNKNOWN = { goals: 'no goal data for this paper, counts as 0', relevance: 'unknown', quality: 'not reviewed, typical value used', prestige: 'unknown for this paper, median used' };

// A sibling button of the row's expand button (never nested in it): tap / Enter / Space.
export function WhyHereButton({ open, onToggle, panelId }) {
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-expanded={open}
      aria-controls={panelId}
      title="Why this paper is at this place in the list"
      className={`shrink-0 px-1.5 py-0.5 min-h-[28px] rounded-md border text-[11px] ${
        open ? 'border-teal-400 bg-teal-50 text-teal-800' : 'border-slate-300 text-slate-600 hover:bg-slate-50'
      }`}
    >
      Why here
    </button>
  );
}

const f2 = (n) => n.toFixed(2);

// The answer to «why here?»: group, each signal's weight × term, the key — and, in
// Balanced, the quality reorder inside the ★ band — plus the model's own main reason.
export function WhyHerePanel({ item, order, meta, mustCount, panelId }) {
  const { rows, key } = explainRow(item, order, meta);
  const balanced = order.preset === 'balanced';
  const grade = String(item.quality_grade || '').toUpperCase();
  const band = scoreToBand(item.relevance_score);
  return (
    <div
      id={panelId}
      data-testid="why-here-panel"
      className="mt-1 rounded-lg border border-teal-200 bg-teal-50/40 px-3 py-2 text-[12px] text-slate-700 space-y-1"
    >
      {GROUP_LINE[item.rank_group] && <div className="font-medium">{GROUP_LINE[item.rank_group](mustCount)}</div>}
      <ul className="space-y-0.5">
        {rows.map((r) => (
          <li key={r.signal} className="flex flex-wrap items-center gap-x-2">
            <span className="w-16 shrink-0">{SIGNAL_LABEL[r.signal]}</span>
            <span aria-hidden="true" className="inline-block w-[60px] shrink-0">
              <span className="block h-1.5 rounded bg-teal-500" style={{ width: `${Math.round(60 * (key > 0 ? r.contribution / key : 0))}px` }} />
            </span>
            <span className="mono">{f2(r.part)} × {Math.round(r.share * 100)}% = {f2(r.contribution)}</span>
            {!r.known && <span className="basis-full pl-[4.5rem] text-[11px] text-slate-500">{UNKNOWN[r.signal]}</span>}
          </li>
        ))}
      </ul>
      <div className="font-medium">
        Key {f2(key)}
        {balanced && grade && band && (
          <span className="font-normal"> · Quality {grade}: reorders it within its ★ {BAND_RANGE[band]} band, never across it</span>
        )}
      </div>
      {item.why_reason && <div>Main model reason: {item.why_reason}</div>}
    </div>
  );
}
