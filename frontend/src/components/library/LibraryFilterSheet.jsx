import { useMemo, useRef, useState } from 'react';
import Sheet from '../ui/Sheet.jsx';
import FilterPicker from './FilterPicker.jsx';
import { FilterChip } from '../../pages/AnnotationVerdict_helpers.jsx';
import { BANDS, BAND_LABEL, bandCounts, facetCounts, prestigeBucketCounts } from '../../utils/relevanceBands.js';

// The one filter surface: a full-screen Sheet on phones, the 420 px right panel from 1024 px. Collection, Tag and
// Show read are server scope: they apply the moment they are picked. The rest (Model guess, Rigor, Goals, Code,
// Journal, Prestige) narrows the loaded list: a chip carries its count, and a facet that comes from a deep review
// has its «Not reviewed» bucket, so no paper disappears without a number. «Show papers» closes the sheet; it
// carries a count only after a client-side facet changed (a collection or tag count is unknown until it loads).
const EMOJI = /^\p{Extended_Pictographic}/u;
const GOALS = [['high', 'Strong'], ['low', 'Weak']];
const RIGOR = [['high', 'A/B'], ['low', 'C/D'], ['nr', 'Not reviewed']];
const CODE = [['found', 'Found'], ['none', 'No link'], ['nr', 'Not reviewed']];
const PRESTIGE = [['high', 'high'], ['low', 'low'], ['new', 'new']];

// How many facets narrow the list now: the Filter button's badge.
function facetCount(f, modelReady) {
  const cf = f.clientFilters;
  const client = modelReady
    ? cf.bands.length + ['goal', 'quality', 'code', 'prestige'].filter((k) => cf[k] !== 'any').length + (cf.venue ? 1 : 0)
    : 0;
  return client + [f.selectedCollection, f.selectedTag, f.includeRead].filter(Boolean).length;
}

function Group({ label, hint, children }) {
  return (
    <fieldset className="border-b border-slate-200 py-3">
      <legend className="mb-1.5 text-sm font-semibold text-slate-700">{label}</legend>
      {hint && <p className="mb-1.5 text-xs text-slate-500">{hint}</p>}
      <div className="flex flex-wrap gap-1.5">{children}</div>
    </fieldset>
  );
}

// Single-select chips with their counts; a bucket with no rows is no chip unless applied (then it stays, to undo).
function Chips({ label, hint, options, counts, value, onPick }) {
  const shown = options.filter(([key]) => counts[key] > 0 || key === value);
  if (!shown.length) return null;
  return (
    <Group label={label} hint={hint}>
      {shown.map(([key, text]) => (
        <FilterChip key={key} active={value === key} onClick={() => onPick(value === key ? 'any' : key)}>{`${text} ${counts[key]}`}</FilterChip>
      ))}
    </Group>
  );
}

function ClientFacets({ f, queue, ctx }) {
  const cf = f.clientFilters;
  const set = (patch) => f.setClientFilters({ ...cf, ...patch });
  const counts = useMemo(() => facetCounts(queue, ctx.goalHigh), [queue, ctx.goalHigh]);
  const bands = useMemo(() => bandCounts(queue, ctx.prestigeFloor), [queue, ctx.prestigeFloor]);
  const prestigeCounts = useMemo(() => prestigeBucketCounts(queue, ctx.prestigeFloor), [queue, ctx.prestigeFloor]);
  const venues = useMemo(() => [...new Set(queue.map((i) => i.venue).filter(Boolean))].sort(), [queue]);
  const [moreOpen, setMoreOpen] = useState(cf.prestige !== 'any');
  const modelBands = BANDS.filter((b) => bands[b] > 0 || cf.bands.includes(b));
  return (
    <>
      <Chips label="Rigor" options={RIGOR} counts={counts.rigor} value={cf.quality} onPick={(quality) => set({ quality })} />
      <Chips label="Goals" hint="Strong = top third of this list" options={GOALS} counts={counts.goal} value={cf.goal} onPick={(goal) => set({ goal })} />
      <Chips label="Code" options={CODE} counts={counts.code} value={cf.code} onPick={(code) => set({ code })} />
      {modelBands.length > 0 && (
        <Group label="Model guess">
          {modelBands.map((b) => (
            <FilterChip key={b} active={cf.bands.includes(b)}
              onClick={() => set({ bands: cf.bands.includes(b) ? cf.bands.filter((x) => x !== b) : [...cf.bands, b] })}>
              {`${BAND_LABEL[b]} ${bands[b]}`}
            </FilterChip>
          ))}
        </Group>
      )}
      {venues.length > 0 && (
        <div className="flex items-center justify-between gap-3 border-b border-slate-200 py-2">
          <label htmlFor="filter-journal" className="text-sm font-semibold text-slate-700">Journal</label>
          <select id="filter-journal" value={cf.venue} onChange={(e) => set({ venue: e.target.value })}
            className="min-w-0 max-w-[14rem] rounded-md border border-slate-300 bg-white px-2 py-1.5 text-sm text-slate-700">
            <option value="">Any</option>
            {venues.map((v) => <option key={v} value={v}>{v}</option>)}
          </select>
        </div>
      )}
      <details className="py-1" open={moreOpen} onToggle={(e) => setMoreOpen(e.currentTarget.open)}>
        <summary className="flex min-h-11 cursor-pointer select-none list-none items-center text-sm font-semibold text-slate-700 [&::-webkit-details-marker]:hidden">More <span aria-hidden="true" className="ml-1">›</span></summary>
        <Chips label="Prestige" options={PRESTIGE} counts={prestigeCounts} value={cf.prestige} onPick={(prestige) => set({ prestige })} />
      </details>
    </>
  );
}

function FilterSheet({ f, collections, tags, queue, ctx, modelReady, serverAvailable, readCount, matchCount, loading, onClose }) {
  const [wide] = useState(() => window.matchMedia?.('(min-width: 1024px)').matches === true);  // the width when it opens
  const startedWith = useRef(JSON.stringify(f.clientFilters));
  const changed = modelReady && JSON.stringify(f.clientFilters) !== startedWith.current;
  const collectionOptions = collections.map((c) => ({ value: c.key, label: c.label, count: c.item_count, depth: c.depth }));
  const pinned = (t) => EMOJI.test(t.tag);
  const tagOptions = [...tags.filter(pinned), ...tags.filter((t) => !pinned(t))].slice(0, 100)
    .map((t) => ({ value: t.tag, label: t.tag, count: t.item_count }));
  return (
    <Sheet title="Filter" variant={wide ? 'side' : 'full'} onClose={onClose}>
      {(collectionOptions.length > 0 || f.selectedCollection) && (
        <FilterPicker name="Collection" noun="collections" options={collectionOptions} value={f.selectedCollection} onChange={f.selectCollection}
          allLabel="All items" unknownLabel="this collection" />
      )}
      {(tagOptions.length > 0 || f.selectedTag) && (
        <FilterPicker name="Tag" noun="tags" options={tagOptions} value={f.selectedTag} onChange={f.selectTag} allLabel="All tags" unknownLabel={f.selectedTag} />
      )}
      {modelReady && queue.length > 0 && <ClientFacets f={f} queue={queue} ctx={ctx} />}
      <label className="flex min-h-11 cursor-pointer items-center gap-2 border-b border-slate-200 text-sm text-slate-700">
        <input type="checkbox" disabled={!serverAvailable} checked={f.includeRead} onChange={(e) => f.setIncludeRead(e.target.checked)}
          className="h-4 w-4 rounded border-slate-300 text-teal-600 focus:ring-teal-500" />
        {`Show read papers (${readCount})`}
      </label>
      <div className="sticky -bottom-4 -mx-4 -mb-4 border-t border-slate-200 bg-white px-4 py-3">
        <button type="button" onClick={onClose} className="min-h-11 w-full rounded-lg bg-teal-700 text-sm font-semibold text-white hover:bg-teal-800">
          {changed && !loading ? `Show ${matchCount} ${matchCount === 1 ? 'paper' : 'papers'}` : 'Show papers'}
        </button>
      </div>
    </Sheet>
  );
}

export default function LibraryFilters(props) {
  const [open, setOpen] = useState(false);
  const n = facetCount(props.f, props.modelReady);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)} aria-haspopup="dialog"
        className="rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm text-slate-700 hover:bg-slate-50">
        {n > 0 ? `Filter ${n}` : 'Filter'}
      </button>
      {open && <FilterSheet {...props} onClose={() => setOpen(false)} />}
    </>
  );
}
