// Pure, client-side filter model for the Library reading queue.
//
// Mirrors zotero_summarizer/domain.py:57-59 (score_to_priority thresholds) so the
// client bands match the server's banding exactly. Everything here is display /
// filter only — callers use `buildPredicate` with `Array.prototype.filter`, which
// preserves the API's order, so the goal-blended ranking is never reshuffled.

export const BAND_MUST = 4.5;
export const BAND_SHOULD = 3.5;
export const BAND_COULD = 2.0;

export const BANDS = ['must_read', 'should_read', 'could_read', 'dont_read'];

export const BAND_LABEL = {
  must_read: 'must',
  should_read: 'should',
  could_read: 'could',
  dont_read: "don't",
};

// One palette, shared in spirit with ScoreHistogram.BAND_BAR and the Annotate
// priority chips, so a band looks the same everywhere.
export const BAND_ACTIVE_CLS = {
  must_read: 'bg-emerald-600 text-white border-emerald-600',
  should_read: 'bg-sky-600 text-white border-sky-600',
  could_read: 'bg-amber-500 text-white border-amber-500',
  dont_read: 'bg-rose-600 text-white border-rose-600',
};

export function scoreToBand(score) {
  if (typeof score !== 'number') return null;     // unscored → no band
  if (score >= BAND_MUST) return 'must_read';
  if (score >= BAND_SHOULD) return 'should_read';
  if (score >= BAND_COULD) return 'could_read';
  return 'dont_read';
}

// Default = identity filter (the list is unchanged).
export const EMPTY_FILTERS = {
  bands: [],        // multi-select subset of BANDS; [] = all bands
  prestige: 'any',  // any | high | low | new
  goal: 'any',      // any | high | low
  minScore: 1,      // 1 = no floor; up to 5 in 0.5 steps
  why: [],          // multi-select of why_reason strings; [] = all
  scored: 'any',    // any | scored | unscored
};

export function isFilterActive(f) {
  if (!f) return false;
  return (
    (f.bands && f.bands.length > 0)
    || f.prestige !== 'any'
    || f.goal !== 'any'
    || (typeof f.minScore === 'number' && f.minScore > 1)
    || (f.why && f.why.length > 0)
    || f.scored !== 'any'
  );
}

// "High prestige" = a KNOWN author/venue reputation at/above the library's
// quality floor (median of known prestige; falls back to the neutral 3.0 on the
// 1–5 scale when no floor is supplied). Single source of the "high prestige"
// rule — used by both the Prestige filter and the card's quality badge so they
// agree. A cold-start / uncited / no-OpenAlex row (prestige_known false) is
// NOT high (no evidence) — never penalised, just unlabelled.
export function isHighPrestige(it, floor = null) {
  const known = it?.prestige_known === true && typeof it.prestige_score === 'number';
  if (!known) return false;
  return floor == null ? it.prestige_score >= 3 : it.prestige_score >= floor;
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

// Build an O(1)-per-row predicate. ctx = { goalHigh: Set, prestigeFloor: number|null }.
export function buildPredicate(filters, ctx) {
  const f = filters || EMPTY_FILTERS;
  const bandSet = new Set(f.bands || []);
  const whySet = new Set(f.why || []);
  const floor = ctx?.prestigeFloor ?? null;
  const goalHigh = ctx?.goalHigh ?? new Set();
  const minScore = typeof f.minScore === 'number' ? f.minScore : 1;

  return (it) => {
    const score = typeof it.relevance_score === 'number' ? it.relevance_score : null;
    const band = scoreToBand(score);

    if (bandSet.size && (band === null || !bandSet.has(band))) return false;
    if (minScore > 1 && (score === null || score < minScore)) return false;

    if (f.prestige !== 'any') {
      const known = it.prestige_known === true && typeof it.prestige_score === 'number';
      const high = isHighPrestige(it, floor);   // single source — matches the card badge
      if (f.prestige === 'new' && known) return false;
      if (f.prestige === 'high' && !high) return false;
      if (f.prestige === 'low' && !(known && !high)) return false;
    }

    // goal_sim only exists on scored unread rows; rows without it match neither
    // high nor low (they carry no goal signal).
    if (f.goal === 'high' && !(typeof it.goal_sim === 'number' && goalHigh.has(it.item_key))) return false;
    if (f.goal === 'low' && !(typeof it.goal_sim === 'number' && !goalHigh.has(it.item_key))) return false;

    if (whySet.size && !whySet.has(it.why_reason)) return false;

    if (f.scored === 'scored' && score === null) return false;
    if (f.scored === 'unscored' && score !== null) return false;

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
  if (typeof f.minScore === 'number' && f.minScore > 1) out.s = String(f.minScore);
  if (f.why && f.why.length) out.w = f.why.join('~');   // '~' never appears in a why label
  if (f.scored && f.scored !== 'any') out.sc = f.scored;
  return out;
}

export function hydrateFilters(searchParams) {
  const get = (k) => searchParams.get(k);
  const bands = (get('b') || '').split(',').filter((x) => BANDS.includes(x));
  const prestige = ['high', 'low', 'new'].includes(get('pr')) ? get('pr') : 'any';
  const goal = ['high', 'low'].includes(get('g')) ? get('g') : 'any';
  const sNum = Number(get('s'));
  const minScore = Number.isFinite(sNum) && sNum > 1 && sNum <= 5 ? sNum : 1;
  const why = (get('w') || '').split('~').filter(Boolean);
  const scored = ['scored', 'unscored'].includes(get('sc')) ? get('sc') : 'any';
  return { bands, prestige, goal, minScore, why, scored };
}
