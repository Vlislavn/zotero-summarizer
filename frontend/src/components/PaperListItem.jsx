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

export default function PaperListItem({ item, isSelected = false, onClick = () => {} }) {
  if (!item) return null;
  const cls = isSelected
    ? 'border-teal-600 bg-teal-50 ring-1 ring-teal-300'
    : 'border-slate-200 bg-white hover:bg-slate-50';
  const derived = item.derived_priority || 'could_read';
  const persisted = item.persisted_priority;
  const flags = Array.isArray(item.flags) ? item.flags : [];

  return (
    <li>
      <button
        type="button"
        onClick={() => onClick(item)}
        className={`w-full text-left border rounded-xl p-3 transition-colors ${cls}`}
      >
        <div className="flex items-start justify-between gap-2">
          <div className="text-sm font-semibold text-slate-900 leading-snug line-clamp-2">
            {item.title || '(untitled)'}
          </div>
          <span className="mono text-slate-400 text-[10px] flex-shrink-0">
            {item.item_key}
          </span>
        </div>
        <div className="mt-1.5 flex flex-wrap gap-1 items-center">
          <span
            className={`px-1.5 py-0.5 rounded text-[10px] font-semibold border ${
              PRIORITY_BADGE[derived] || 'bg-slate-100 text-slate-700 border-slate-200'
            }`}
            title="Derived priority"
          >
            {derived}
          </span>
          {typeof item.derived_score === 'number' && (
            <span className="text-[10px] mono text-slate-500">
              {item.derived_score.toFixed(2)}
            </span>
          )}
          {persisted && persisted !== derived && (
            <span
              className={`px-1.5 py-0.5 rounded text-[10px] border ${
                PRIORITY_BADGE[persisted] || 'bg-slate-100 text-slate-700 border-slate-200'
              }`}
              title="Persisted priority"
            >
              p:{persisted}
            </span>
          )}
          {item.is_direct_user_verdict && (
            <span
              className="px-1.5 py-0.5 rounded text-[10px] border bg-emerald-50 text-emerald-700 border-emerald-200"
              title="Direct user verdict"
            >
              verdict
            </span>
          )}
          {item.is_manual_override && (
            <span
              className="px-1.5 py-0.5 rounded text-[10px] border bg-violet-50 text-violet-700 border-violet-200"
              title="Manual override applied"
            >
              override
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
