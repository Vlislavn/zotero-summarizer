import { FilterChip } from '../../pages/AnnotationVerdict_helpers.jsx';
import { BANDS, BAND_LABEL } from '../../utils/relevanceBands.js';

// Smart client-side filters for the Library reading queue.
// Compact + progressive disclosure (Hick's Law): the headline facets that vary
// day-to-day (model guess, goal-match, quality) sit on the quick row; the
// lower-frequency Prestige + Journal facets live behind an "Advanced" <details>.
// The model-guess chips carry their counts (one band control). Clearing is owned
// by the page's single "Clear all". A controlled component: owns no state, calls
// onChange(nextFilters).

const PRESTIGE_OPTS = [
  { value: 'high', label: 'high' },
  { value: 'low', label: 'low' },
  { value: 'new', label: 'new' },
];
const GOAL_OPTS = [
  { value: 'high', label: 'strong' },
  { value: 'low', label: 'weak' },
];
// Deep-review rigor letter: A/B = high rigor, C/D = weak. Only shown when
// some rows carry a grade (i.e. have been deep-reviewed).
const QUALITY_OPTS = [
  { value: 'high', label: 'A/B' },
  { value: 'low', label: 'C/D' },
];

// `stacked`: the label sits above its chips (a long chip row then fits one line on a phone).
function Cluster({ label, stacked = false, children }) {
  return (
    <span className={stacked ? 'inline-flex flex-col gap-1' : 'inline-flex items-center gap-1.5'}>
      <span className="text-[11px] uppercase tracking-wider text-slate-400 font-semibold">{label}</span>
      <span className="flex flex-wrap gap-1">{children}</span>
    </span>
  );
}

export default function LibraryFilterBar({
  filters, onChange, venueOptions = [], prestigeCounts = null, bandCounts = null,
  goalEnabled = false, qualityEnabled = false,
}) {
  const set = (patch) => onChange({ ...filters, ...patch });
  // Single-select cluster: clicking the active value clears it back to 'any'.
  const single = (field, value) => set({ [field]: filters[field] === value ? 'any' : value });
  const toggle = (field, value) => set({
    [field]: filters[field].includes(value)
      ? filters[field].filter((x) => x !== value)
      : [...filters[field], value],
  });
  // Prestige chip label carries its bucket count so an empty bucket is visible
  // before the click (the old bug: high/low silently blanked a preprint library).
  const prestigeLabel = (o) => (
    prestigeCounts && typeof prestigeCounts[o.value] === 'number'
      ? `${o.label} (${prestigeCounts[o.value]})` : o.label
  );

  const modelBands = BANDS.filter((b) => bandCounts?.[b] > 0 || filters.bands.includes(b));
  const advancedActive = filters.prestige !== 'any' || !!filters.venue;

  return (
    <div className="rounded-xl border border-slate-200 bg-slate-50/60 p-2.5">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        {/* Model guess — multi-select bands with their counts. A band with no rows is
            no chip (a dead control), unless it is applied — then it stays, to be undone. */}
        {modelBands.length > 0 && (
          <Cluster label="Model guess" stacked>
            {modelBands.map((band) => (
              <FilterChip
                key={band}
                active={filters.bands.includes(band)}
                onClick={() => toggle('bands', band)}
              >
                {`${BAND_LABEL[band]} ${bandCounts?.[band] ?? 0}`}
              </FilterChip>
            ))}
          </Cluster>
        )}

        {/* Goal-match — only when a goal signal exists (corpus on, scored rows). */}
        {goalEnabled && (
          <Cluster label="Matches my goals">
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

        {/* Rigor — single-select; only when some rows carry a deep-review grade. */}
        {qualityEnabled && (
          <Cluster label="Rigor">
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
      </div>

      <details open={advancedActive} className="mt-1.5 text-xs">
        <summary className="cursor-pointer select-none text-slate-500 hover:text-slate-800 w-fit">
          Advanced filters
        </summary>
        <div className="mt-2 flex flex-wrap items-center gap-x-5 gap-y-2">
          {/* Prestige — single-select; lowest-frequency facet, so it lives here.
              Chips carry a live bucket count (author h-index OR citations count as
              evidence, so a preprint by a known author is filterable, not blanked). */}
          <Cluster label="Prestige">
            {PRESTIGE_OPTS.map((o) => (
              <FilterChip
                key={o.value}
                active={filters.prestige === o.value}
                onClick={() => single('prestige', o.value)}
              >
                {prestigeLabel(o)}
              </FilterChip>
            ))}
          </Cluster>

          {/* Journal / venue — single-select from the live data (Hick's Law: a
              select, not a chip-per-venue row). Empty = preprints / no journal. */}
          {venueOptions.length > 0 && (
            <Cluster label="Journal">
              <select
                value={filters.venue}
                onChange={(e) => set({ venue: e.target.value })}
                className="text-xs rounded-md border border-slate-300 bg-white px-1.5 py-0.5 max-w-[16rem] text-slate-700"
              >
                <option value="">any</option>
                {venueOptions.map((v) => (
                  <option key={v} value={v}>{v}</option>
                ))}
              </select>
            </Cluster>
          )}
        </div>
      </details>
    </div>
  );
}
