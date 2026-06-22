import { FilterChip } from '../../pages/AnnotationVerdict_helpers.jsx';
import { isFilterActive } from '../../utils/relevanceBands.js';

// Smart client-side filters for the Library reading queue.
// Compact + progressive disclosure (Hick's Law): the headline facets that vary
// day-to-day (goal-match, quality) sit on the quick row; the lower-frequency
// Prestige + Why facets live behind an "Advanced" <details>. Band filtering is
// owned by the ScoreHistogram bars (one band control, not a duplicate chip row),
// and the raw Score≥ / scored-state knobs were removed (the histogram + Rescore
// already cover them). A controlled component: owns no state, calls
// onChange(nextFilters) / onClear().

const PRESTIGE_OPTS = [
  { value: 'high', label: 'high' },
  { value: 'low', label: 'low' },
  { value: 'new', label: 'new' },
];
const GOAL_OPTS = [
  { value: 'high', label: 'high' },
  { value: 'low', label: 'low' },
];
// Deep-review quality grade: A/B = "quality papers", C/D = weak. Only shown when
// some rows carry a grade (i.e. have been deep-reviewed).
const QUALITY_OPTS = [
  { value: 'high', label: 'A/B' },
  { value: 'low', label: 'C/D' },
];

function Cluster({ label, children }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className="text-[11px] uppercase tracking-wider text-slate-400 font-semibold">{label}</span>
      <span className="flex flex-wrap gap-1">{children}</span>
    </span>
  );
}

export default function LibraryFilterBar({
  filters, onChange, whyOptions = [], goalEnabled = false, qualityEnabled = false, onClear,
}) {
  const set = (patch) => onChange({ ...filters, ...patch });
  // Single-select cluster: clicking the active value clears it back to 'any'.
  const single = (field, value) => set({ [field]: filters[field] === value ? 'any' : value });
  const toggle = (field, value) => set({
    [field]: filters[field].includes(value)
      ? filters[field].filter((x) => x !== value)
      : [...filters[field], value],
  });

  const active = isFilterActive(filters);
  const advancedActive = filters.prestige !== 'any' || filters.why.length > 0;

  return (
    <div className="mb-3 rounded-xl border border-slate-200 bg-slate-50/60 p-2.5">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        {/* Band filtering lives on the ScoreHistogram bars (one band control). */}

        {/* Goal-match — only when a goal signal exists (corpus on, scored rows). */}
        {goalEnabled && (
          <Cluster label="Goal">
            {GOAL_OPTS.map((o) => (
              <FilterChip
                key={o.value}
                active={filters.goal === o.value}
                onClick={() => single('goal', o.value)}
              >
                {o.label}
              </FilterChip>
            ))}
          </Cluster>
        )}

        {/* Quality — single-select; only when some rows carry a deep-review grade. */}
        {qualityEnabled && (
          <Cluster label="Quality">
            {QUALITY_OPTS.map((o) => (
              <FilterChip
                key={o.value}
                active={filters.quality === o.value}
                onClick={() => single('quality', o.value)}
              >
                {o.label}
              </FilterChip>
            ))}
          </Cluster>
        )}

        {/* Close-the-loop (Zeigarnik): a way out. The shown/total count lives with
            the list it describes (ReadNextView), not duplicated here. */}
        {active && (
          <button
            type="button"
            onClick={onClear}
            className="ml-auto px-2 py-0.5 rounded-md border border-slate-300 bg-white text-slate-700 hover:bg-slate-100 text-[11px] font-medium"
          >
            Clear filters
          </button>
        )}
      </div>

      <details open={advancedActive} className="mt-1.5 text-xs">
        <summary className="cursor-pointer select-none text-slate-500 hover:text-slate-800 w-fit">
          Advanced filters
        </summary>
        <div className="mt-2 flex flex-wrap items-center gap-x-5 gap-y-2">
          {/* Prestige — single-select; lowest-frequency facet, so it lives here. */}
          <Cluster label="Prestige">
            {PRESTIGE_OPTS.map((o) => (
              <FilterChip
                key={o.value}
                active={filters.prestige === o.value}
                onClick={() => single('prestige', o.value)}
              >
                {o.label}
              </FilterChip>
            ))}
          </Cluster>

          {/* Why (top reason) — multi-select, built from the live data. */}
          {whyOptions.length > 0 && (
            <Cluster label="Why">
              {whyOptions.map((w) => (
                <FilterChip key={w} active={filters.why.includes(w)} onClick={() => toggle('why', w)}>
                  {w}
                </FilterChip>
              ))}
            </Cluster>
          )}
        </div>
      </details>
    </div>
  );
}
