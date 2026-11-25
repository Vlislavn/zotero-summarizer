// Pure, client-side filter + sort model for the Library reading queue.
//
// Mirrors zotero_summarizer/domain.py:57-59 (score_to_priority thresholds) so the
// client bands match the server's banding exactly. Filtering uses `buildPredicate`
// with `Array.prototype.filter`, which preserves the API's order — the goal-blended
// ranking is only reshuffled when the user picks an EXPLICIT sort (`sortQueue`,
// Zotero-column semantics); the default 'best' sort is the identity.

const BAND_MUST = 4.5;
const BAND_SHOULD = 3.5;
const BAND_COULD = 2.0;

export const BANDS = ['must_read', 'should_read', 'could_read', 'dont_read'];

export const BAND_LABEL = {
  must_read: 'must',
  should_read: 'should',
  could_read: 'could',
  dont_read: 'Not interested',
};

export function scoreToBand(score) {
  if (typeof score !== 'number') return null;     // unscored → no band
  if (score >= BAND_MUST) return 'must_read';
  if (score >= BAND_SHOULD) return 'should_read';
  if (score >= BAND_COULD) return 'could_read';
  return 'dont_read';
}

function effectiveBand(it, floor) {
  const band = scoreToBand(it.relevance_score);
  if (floor == null || it.prestige_known !== true || typeof it.prestige_score !== 'number'
      || it.prestige_score >= floor) return band;
  if (band === 'must_read') return 'should_read';
  if (band === 'should_read') return 'could_read';
  return band;
}

// "Cool" = a high-relevance pick (must/should-read band) the user hasn't decided
// yet: no fleet proposal (proposed_verdict) and no own label (user_priority). The
// auto-review ("Review cool papers") loops the fleet over exactly these.
export function isCoolUndecided(it, floor = null) {
  if (!it || it.proposed_verdict || it.user_priority) return false;
  const band = effectiveBand(it, floor);
  return band === 'must_read' || band === 'should_read';
}

// The item_keys of every cool-undecided row, in queue order. The auto-review loop
// pins these to the fleet (so it reviews the SAME rows the UI counts — a cool paper
// can sit deep in the blended queue while the band-agnostic fleet selector would
// review higher-blended could_read rows first).
export function coolUndecidedKeys(items, floor = null) {
  return (items || []).filter((it) => isCoolUndecided(it, floor)).map((it) => it.item_key);
}

// Default = identity filter (the list is unchanged).
export const EMPTY_FILTERS = {
  bands: [],        // multi-select subset of BANDS; [] = all bands
  prestige: 'any',  // any | high | low | new
  goal: 'any',      // any | high | low
  quality: 'any',   // any | high (deep-review grade A/B) | low (C/D) | nr (no grade: not reviewed)
  code: 'any',      // any | found (code link found) | none (reviewed, no link) | nr (not reviewed)
  venue: '',        // exact journal/venue name; '' = any
};

export function isFilterActive(f) {
  if (!f) return false;
  return (
    (f.bands && f.bands.length > 0)
    || f.prestige !== 'any'
    || f.goal !== 'any'
    || f.quality !== 'any'
    || f.code !== 'any'
    || (f.venue && f.venue.length > 0)
  );
}

// "High prestige" = a row with prestige EVIDENCE (an OpenAlex citation percentile
// OR a known author h-index) scoring at/above the library's quality floor (median
// of known prestige; falls back to the neutral 3.0 on the 1–5 scale when no floor
// is supplied). Single source of the "high prestige" rule — used by both the
// Prestige filter and the card's quality badge so they agree. Keying on
// `prestige_evidence` (not the citation-only `prestige_known`) is what lets an
// uncited preprint by a high-h-index author count as high prestige; a row with NO
// signal at all (prestige_evidence false) is never high — unlabelled, not penalised.
function isHighPrestige(it, floor = null) {
  const ev = it?.prestige_evidence === true && typeof it.prestige_score === 'number';
  if (!ev) return false;
  return floor == null ? it.prestige_score >= 3 : it.prestige_score >= floor;
}

// Count rows per Prestige bucket so the filter chips can show "high (N)" — a row
// with no prestige signal lands in `new`. Makes an empty bucket visible BEFORE the
// click (the prior bug: filtering high/low silently blanked a preprint library).
export function prestigeBucketCounts(items, floor = null) {
  const c = { high: 0, low: 0, new: 0 };
  for (const it of items || []) {
    const ev = it?.prestige_evidence === true && typeof it.prestige_score === 'number';
    if (!ev) { c.new += 1; continue; }
    if (isHighPrestige(it, floor)) c.high += 1;
    else c.low += 1;
  }
  return c;
}

// Rows per model-guess band (the Model-guess chips show "must (12)"). Same banding
// as the predicate (prestige floor included), so a chip's count is what clicking it
// keeps. Unscored rows have no band and are not counted.
export function bandCounts(items, floor = null) {
  const c = { must_read: 0, should_read: 0, could_read: 0, dont_read: 0 };
  for (const it of items || []) {
    const band = effectiveBand(it, floor);
    if (band) c[band] += 1;
  }
  return c;
}

// Keys of the top (1 - pct) fraction by goal_sim, among rows that HAVE a numeric
// goal_sim. Returns an empty Set when no row carries goal_sim (corpus disabled,
// or only read rows present) — callers then hide the goal control entirely.
export function goalHighKeys(items, pct = 0.66) {
  const withSim = (items || []).filter((i) => typeof i.goal_sim === 'number');
  if (withSim.length === 0) return new Set();
  const sorted = [...withSim].sort((a, b) => a.goal_sim - b.goal_sim);
  const cut = sorted[Math.min(Math.floor(sorted.length * pct), sorted.length - 1)].goal_sim;
  return new Set(withSim.filter((i) => i.goal_sim >= cut).map((i) => i.item_key));
}

// A paper's code bucket, from the row's `has_code` (true found · false reviewed, no link · null not reviewed).
const codeBucket = (it) => (it.has_code === true ? 'found' : it.has_code === false ? 'none' : 'nr');
const rigorBucket = (it) => {
  const grade = String(it.quality_grade || '').toUpperCase();
  return grade === 'A' || grade === 'B' ? 'high' : grade === 'C' || grade === 'D' ? 'low' : 'nr';
};

// Rows per bucket of the facets that come from a deep review or the goal signal, so a chip says how many it
// keeps BEFORE the click and the buckets add up to the list (Rigor and Code: the papers without a review are
// their own `nr` bucket, never silently dropped). Goals: strong / weak among rows that carry goal_sim.
export function facetCounts(items, goalHigh = new Set()) {
  const c = { rigor: { high: 0, low: 0, nr: 0 }, code: { found: 0, none: 0, nr: 0 }, goal: { high: 0, low: 0 } };
  for (const it of items || []) {
    c.rigor[rigorBucket(it)] += 1;
    c.code[codeBucket(it)] += 1;
    if (typeof it.goal_sim === 'number') c.goal[goalHigh.has(it.item_key) ? 'high' : 'low'] += 1;
  }
  return c;
}

// Build an O(1)-per-row predicate. ctx = { goalHigh: Set, prestigeFloor: number|null }.
export function buildPredicate(filters, ctx) {
  const f = filters || EMPTY_FILTERS;
  const bandSet = new Set(f.bands || []);
  const floor = ctx?.prestigeFloor ?? null;
  const goalHigh = ctx?.goalHigh ?? new Set();

  return (it) => {
    const band = effectiveBand(it, floor);

    if (bandSet.size && (band === null || !bandSet.has(band))) return false;

    if (f.prestige !== 'any') {
      const ev = it.prestige_evidence === true && typeof it.prestige_score === 'number';
      const high = isHighPrestige(it, floor);   // single source — matches the card badge
      if (f.prestige === 'new' && ev) return false;       // 'new' = no prestige signal yet
      if (f.prestige === 'high' && !high) return false;
      if (f.prestige === 'low' && !(ev && !high)) return false;
    }

    // Journal/venue — exact match against the row's publicationTitle ('' = any).
    if (f.venue && it.venue !== f.venue) return false;

    // goal_sim only exists on scored unread rows; rows without it match neither
    // high nor low (they carry no goal signal).
    if (f.goal === 'high' && !(typeof it.goal_sim === 'number' && goalHigh.has(it.item_key))) return false;
    if (f.goal === 'low' && !(typeof it.goal_sim === 'number' && !goalHigh.has(it.item_key))) return false;

    // Deep-review quality grade (only set on reviewed rows): the ungraded rows are the `nr` bucket,
    // so choosing A/B or C/D hides them in plain sight (the sheet shows their count), not silently.
    if (f.quality && f.quality !== 'any' && rigorBucket(it) !== f.quality) return false;
    if (f.code && f.code !== 'any' && codeBucket(it) !== f.code) return false;

    return true;
  };
}

// URL (de)serialize — compact keys, defaults omitted, so a clean queue has a
// clean URL and an active filter set is shareable / survives reload.
export function serializeFilters(f) {
  const out = {};
  if (f.bands && f.bands.length) out.b = f.bands.join(',');
  if (f.prestige && f.prestige !== 'any') out.pr = f.prestige;
  if (f.goal && f.goal !== 'any') out.g = f.goal;
  if (f.quality && f.quality !== 'any') out.q = f.quality;
  if (f.code && f.code !== 'any') out.code = f.code;
  if (f.venue) out.vn = f.venue;
  return out;
}

export function hydrateFilters(searchParams) {
  const get = (k) => searchParams.get(k);
  const bands = (get('b') || '').split(',').filter((x) => BANDS.includes(x));
  const prestige = ['high', 'low', 'new'].includes(get('pr')) ? get('pr') : 'any';
  const goal = ['high', 'low'].includes(get('g')) ? get('g') : 'any';
  const quality = ['high', 'low', 'nr'].includes(get('q')) ? get('q') : 'any';
  const code = ['found', 'none', 'nr'].includes(get('code')) ? get('code') : 'any';
  const venue = get('vn') || '';
  return { bands, prestige, goal, quality, code, venue };
}

// ---------- Zotero-column sort (explicit user sort over the loaded queue) ------
// Field names deliberately mirror Zotero's item-table columns (Jakob's Law:
// Title / Creator / Publication / Year / Date Added), so the mental model
// transfers 1:1. 'best' = the server's blended ranking, untouched (identity).
export const SORT_FIELDS = [
  { key: 'best', label: 'Best match' },
  { key: 'title', label: 'Title' },
  { key: 'creator', label: 'Creator' },
  { key: 'publication', label: 'Publication' },
  { key: 'year', label: 'Year' },
  { key: 'dateAdded', label: 'Date Added' },
];

// First-click direction per field, matching Zotero: text columns ascend,
// date-ish columns show newest first.
export const SORT_DEFAULT_DIR = {
  title: 'asc', creator: 'asc', publication: 'asc', year: 'desc', dateAdded: 'desc',
};

export const DEFAULT_SORT = { field: 'best', dir: 'desc' };

// publication_date is Zotero's free-form `date` string — the year is the first
// 4-digit run, wherever it sits ("2024-05-01", "May 2024", "2024").
function pubYear(it) {
  const m = /\b(\d{4})\b/.exec(it.publication_date || '');
  return m ? Number(m[1]) : null;
}

const SORT_VALUE = {
  title: (it) => (it.title || '').toLowerCase() || null,
  creator: (it) => (it.authors || '').toLowerCase() || null,
  publication: (it) => (it.venue || '').toLowerCase() || null,
  year: pubYear,
  // Zotero dateAdded is "YYYY-MM-DD HH:MM:SS" — lexicographic order IS
  // chronological order, no parsing needed.
  dateAdded: (it) => it.date_added || null,
};

// Stable explicit sort; rows missing the field sink to the END in BOTH
// directions (Zotero-like: an empty cell never leads the column).
export function sortQueue(items, sort) {
  const field = sort?.field || 'best';
  if (field === 'best') return items;
  const value = SORT_VALUE[field];
  const sign = sort.dir === 'asc' ? 1 : -1;
  return [...(items || [])].sort((a, b) => {
    const va = value(a);
    const vb = value(b);
    if (va == null && vb == null) return 0;
    if (va == null) return 1;
    if (vb == null) return -1;
    const cmp = typeof va === 'number' ? va - vb : va.localeCompare(vb);
    return cmp * sign;
  });
}

// URL (de)serialize for the sort — same compact-keys convention as the filters.
export function serializeSort(sort) {
  const out = {};
  if (sort && sort.field !== 'best' && SORT_VALUE[sort.field]) {
    out.s = sort.field;
    if (sort.dir !== SORT_DEFAULT_DIR[sort.field]) out.sd = sort.dir;
  }
  return out;
}

export function hydrateSort(searchParams) {
  const field = searchParams.get('s');
  if (!field || !SORT_VALUE[field]) return DEFAULT_SORT;
  const sd = searchParams.get('sd');
  const dir = sd === 'asc' || sd === 'desc' ? sd : SORT_DEFAULT_DIR[field];
  return { field, dir };
}
