// Goal-Gradient progress indicator for the first-run wizard. Showing how close
// the user is to "done" (and crediting completed steps) pulls them toward
// finishing — the wizard is short by design, so the bar reads as nearly-full
// almost immediately.

const STEP_LABELS = ['Connect Zotero', 'Connect LLM', 'Describe research'];

export default function StepProgress({ current, validity, maxReached = 0 }) {
  // current is the 0-based active step index. A step earns its green check only
  // once it is BOTH valid AND has been reached (i <= maxReached) — otherwise a
  // step whose prefilled defaults are already valid (e.g. step 3 "Describe
  // research") would render as "done" before the user ever navigates to it.
  return (
    <ol className="flex items-center gap-2" aria-label="Setup progress">
      {STEP_LABELS.map((label, i) => {
        const done = Boolean(validity?.[i]) && i <= maxReached;
        const active = i === current;
        const stateCls = active
          ? 'bg-forest-800 text-white border-forest-800'
          : done
            ? 'bg-emerald-500 text-white border-emerald-500'
            : 'bg-white text-slate-400 border-slate-300';
        return (
          <li key={label} className="flex items-center gap-2 flex-1 min-w-0">
            <span
              className={`flex items-center justify-center h-6 w-6 shrink-0 rounded-full border text-xs font-bold ${stateCls}`}
              aria-hidden
            >
              {done && !active ? '✓' : i + 1}
            </span>
            <span
              className={`text-xs font-medium truncate ${
                active ? 'text-slate-900' : done ? 'text-emerald-700' : 'text-slate-400'
              }`}
            >
              {label}
            </span>
            {i < STEP_LABELS.length - 1 && (
              <span
                className={`h-px flex-1 ${done ? 'bg-emerald-300' : 'bg-slate-200'}`}
                aria-hidden
              />
            )}
          </li>
        );
      })}
    </ol>
  );
}
