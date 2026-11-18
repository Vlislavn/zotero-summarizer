import { BAND_LABEL, DEFAULT_SORT, SORT_FIELDS } from '../../utils/relevanceBands.js';

// What narrows or orders the list right now, one removable chip each, and the page's one «Clear all».
// `f` is the useLibraryFilters value; the smart filters count only while the model is ready
// (otherwise the list is not filtered by them).
const GOAL = { high: 'strong', low: 'weak' };
const RIGOR = { high: 'A/B', low: 'C/D', nr: 'Not reviewed' };
const CODE = { found: 'found', none: 'no link', nr: 'Not reviewed' };

function chips(f, collectionName, modelReady) {
  const { clientFilters: cf, setClientFilters, sort } = f;
  const out = [];
  const add = (label, remove) => out.push({ label, remove });
  if (f.search) add(`Search “${f.search}”`, f.clearSearch);
  if (f.selectedCollection) add(`Collection ${collectionName || 'this collection'}`, () => f.selectCollection(''));
  if (f.selectedTag) add(`Tag ${f.selectedTag}`, () => f.selectTag(''));
  if (f.includeRead) add('Show read', () => f.setIncludeRead(false));
  if (modelReady) {
    cf.bands.forEach((b) => add(`Model guess ${BAND_LABEL[b]}`, () => setClientFilters({ ...cf, bands: cf.bands.filter((x) => x !== b) })));
    if (cf.goal !== 'any') add(`Goals ${GOAL[cf.goal]}`, () => setClientFilters({ ...cf, goal: 'any' }));
    if (cf.quality !== 'any') add(`Rigor ${RIGOR[cf.quality]}`, () => setClientFilters({ ...cf, quality: 'any' }));
    if (cf.code !== 'any') add(`Code ${CODE[cf.code]}`, () => setClientFilters({ ...cf, code: 'any' }));
    if (cf.prestige !== 'any') add(`Prestige ${cf.prestige}`, () => setClientFilters({ ...cf, prestige: 'any' }));
    if (cf.venue) add(`Journal ${cf.venue}`, () => setClientFilters({ ...cf, venue: '' }));
  }
  if (sort.field !== DEFAULT_SORT.field) {
    const name = SORT_FIELDS.find((o) => o.key === sort.field)?.label || sort.field;
    add(`Sort ${name} ${sort.dir === 'asc' ? '↑' : '↓'}`, () => f.setSort(DEFAULT_SORT));
  }
  return out;
}

export default function AppliedFilters({ f, collectionName = '', modelReady = true }) {
  const applied = chips(f, collectionName, modelReady);
  if (!applied.length) return null;
  return (
    <div className="mt-3 flex flex-wrap items-center gap-1.5">
      <ul aria-label="Applied filters" className="contents">
        {applied.map(({ label, remove }) => (
          <li key={label}>
            <button
              type="button"
              onClick={remove}
              aria-label={`Remove ${label}`}
              className="inline-flex max-w-full items-center gap-1.5 rounded-full border border-slate-300 bg-white px-2.5 py-1 text-xs text-slate-700 hover:bg-slate-100"
            >
              <span className="truncate">{label}</span>
              <span aria-hidden="true">×</span>
            </button>
          </li>
        ))}
      </ul>
      <button
        type="button"
        onClick={f.clearAll}
        className="rounded-lg bg-slate-200 px-3 py-1 text-xs text-slate-700 hover:bg-slate-300"
      >
        Clear all
      </button>
    </div>
  );
}
