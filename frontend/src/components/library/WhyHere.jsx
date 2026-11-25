import { SIGNAL_LABEL, explainRow } from '../../utils/readNextOrder.js';
import { GROUP_LINE } from './readNextCopy.js';
import { scoreToBand } from '../../utils/relevanceBands.js';

const BAND_RANGE = { must_read: '4.5–5', should_read: '3.5–4.5', could_read: '2.0–3.5', dont_read: '0–2.0' };
const UNKNOWN = { goals: 'no goal data for this paper, counts as 0', relevance: 'unknown', quality: 'not reviewed, typical value used', prestige: 'unknown for this paper, median used' };

const f2 = (n) => n.toFixed(2);

// The answer to «why here?», shown as «Why it's here» inside the expanded paper: group,
// each signal's weight × term, the key — and, in Balanced, the rigor reorder inside the
// ★ band — plus the model's own main reason.
export function WhyHerePanel({ item, order, meta, mustCount }) {
  const { rows, key } = explainRow(item, order, meta);
  const balanced = order.preset === 'balanced';
  const grade = String(item.quality_grade || '').toUpperCase();
  const band = scoreToBand(item.relevance_score);
  return (
    <div
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
          <span className="font-normal"> · Rigor {grade}: reorders it within its ★ {BAND_RANGE[band]} band, never across it</span>
        )}
      </div>
      {item.why_reason && <div>Main model reason: {item.why_reason}</div>}
    </div>
  );
}
