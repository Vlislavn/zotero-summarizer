import { pretty } from '../utils/priorityLabels.js';

// One row in the paper list on the left side of /annotate.
// Props: { item, isSelected, onClick }
const PRIORITY_BADGE = {
  must_read: 'bg-emerald-100 text-emerald-800 border-emerald-200',
  should_read: 'bg-sky-100 text-sky-800 border-sky-200',
  could_read: 'bg-amber-100 text-amber-800 border-amber-200',
  dont_read: 'bg-rose-100 text-rose-800 border-rose-200',
};

const FLAG_BADGE =
  'bg-violet-50 text-violet-700 border-violet-200';

export default function PaperListItem({
  item,
  isSelected = false,
  onClick = () => {},
  effectiveSource = null,
}) {
  if (!item) return null;
  const cls = isSelected
    ? 'border-teal-600 bg-teal-50 ring-1 ring-teal-300'
    : 'border-slate-200 bg-white hover:bg-slate-50';
  const derived = item.derived_priority || 'could_read';
  // The effective priority (manual verdict wins over derived) is the
  // primary thing shown + filtered on, so a manual label never visibly
  // reverts to the auto value after a Refresh-labels re-derivation.
  const effective = item.effective_priority || item.persisted_priority || derived;
  // 'orphaned' has a dedicated chip below; don't render it twice.
  const flags = (Array.isArray(item.flags) ? item.flags : []).filter(
    (f) => f !== 'orphaned',
  );
  // One badge for "the user's hand is on this label", folding three provenance
  // signals (effective-labels source, golden-CSV direct verdict, manual
  // override) that used to render as three near-identical chips. The row needs
  // only the at-a-glance "this is yours" fact; per-source nuance lives in the
  // detail panel's provenance breakdown.
  const isUserLabel =
    effectiveSource === 'user'
    || item.is_user_override === true
    || item.is_direct_user_verdict === true
    || item.is_manual_override === true;
  const isOrphaned = item.orphaned === true;

  return (
    <li>
      <button
        type="button"
        onClick={() => onClick(item)}
        className={`w-full text-left border rounded-xl p-3 transition-colors ${cls}`}
      >
        <div className="text-sm font-semibold text-slate-900 leading-snug line-clamp-2">
          {item.title || '(untitled)'}
        </div>
        <div className="mt-1.5 flex flex-wrap gap-1 items-center">
          <span
            className={`px-1.5 py-0.5 rounded text-[10px] font-semibold border ${
              PRIORITY_BADGE[effective] || 'bg-slate-100 text-slate-700 border-slate-200'
            }`}
            title={item.is_user_override ? 'Your label (wins over derived)' : 'Priority'}
          >
            {pretty(effective)}
          </span>
          {isOrphaned && (
            <span
              className="px-1.5 py-0.5 rounded text-[10px] font-semibold border bg-amber-100 text-amber-800 border-amber-300"
              title="Your label — paper no longer in the current set, but kept and still editable"
            >
              orphaned
            </span>
          )}
          {isUserLabel && (
            <span
              className="px-1.5 py-0.5 rounded text-[10px] font-semibold border bg-emerald-100 text-emerald-800 border-emerald-300"
              title="Your label — used as ground truth for retraining (overrides the model's derived value)."
              aria-label="Your label, used as ground truth for retraining"
            >
              ★ yours
            </span>
          )}
          {flags.map((f) => (
            <span
              key={f}
              className={`px-1.5 py-0.5 rounded text-[10px] border ${FLAG_BADGE}`}
              title={`Flag: ${f}`}
            >
              {f}
            </span>
          ))}
        </div>
      </button>
    </li>
  );
}
