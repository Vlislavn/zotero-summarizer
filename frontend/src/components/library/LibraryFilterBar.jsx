import { FilterChip } from '../../pages/AnnotationVerdict_helpers.jsx';
import { BANDS, BAND_LABEL, BAND_ACTIVE_CLS, isFilterActive } from '../../utils/relevanceBands.js';

// Smart client-side filters for the Library reading queue (Phase 1).
// Compact + progressive disclosure (Hick's Law): the headline facets live in a
// single quick row grouped by labelled clusters (Law of Common Region / Miller's
// chunking); the rarer controls sit behind an "Advanced" <details> — the same
// house pattern as Annotate's "Advanced filters". A controlled component: it owns
// no state, it calls `onChange(nextFilters)` / `onClear()`.
//
// Filters map to exactly the three groups requested: relevance (bands + score +
// scored), goal & quality (goal-match + why), prestige.

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
const SCORED_OPTS = [
  { value: 'scored', label: 'scored' },
  { value: 'unscored', label: 'unscored' },
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
  filters, onChange, whyOptions = [], goalEnabled = false, qualityEnabled = false,
  rawCount = 0, shownCount = 0, onClear,
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
  const advancedActive = filters.minScore > 1 || filters.why.length > 0 || filters.scored !== 'any';

  return (
    <div className="mb-3 rounded-xl border border-slate-200 bg-slate-50/60 p-2.5">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        {/* Band — multi-select; mirrors the clickable histogram. */}
        <Cluster label="Band">
          {BANDS.map((b) => (
            <FilterChip
              key={b}
              active={filters.bands.includes(b)}
              activeCls={BAND_ACTIVE_CLS[b]}
              onClick={() => toggle('bands', b)}
            >
              {BAND_LABEL[b]}
            </FilterChip>
          ))}
        </Cluster>

        {/* Prestige — single-select. */}
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

        {/* Feedback + close-the-loop (Zeigarnik): what's left + a way out. */}
        {active && (
          <span className="ml-auto flex items-center gap-2 text-[11px] text-slate-500">
            <span>Showing <strong className="text-slate-700">{shownCount}</strong> of {rawCount}</span>
            <button
              type="button"
              onClick={onClear}
              className="px-2 py-0.5 rounded-md border border-slate-300 bg-white text-slate-700 hover:bg-slate-100 font-medium"
            >
              Clear filters
            </button>
          </span>
        )}
      </div>

      <details open={advancedActive} className="mt-1.5 text-xs">
        <summary className="cursor-pointer select-none text-slate-500 hover:text-slate-800 w-fit">
          Advanced filters
        </summary>
        <div className="mt-2 flex flex-wrap items-center gap-x-5 gap-y-2">
          {/* Min relevance score. */}
          <span className="inline-flex items-center gap-2">
            <span className="text-[11px] uppercase tracking-wider text-slate-400 font-semibold">Score ≥</span>
            <input
              type="range"
              min="1"
              max="5"
              step="0.5"
              value={filters.minScore}
              onChange={(e) => set({ minScore: Number(e.target.value) })}
              className="w-28 accent-teal-600"
            />
            <span className="mono text-slate-600 w-6">{filters.minScore.toFixed(1)}</span>
          </span>

          {/* Scored / unscored. */}
          <Cluster label="Scored">
            {SCORED_OPTS.map((o) => (
              <FilterChip
                key={o.value}
                active={filters.scored === o.value}
                onClick={() => single('scored', o.value)}
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
