// Read-next order presets: the client-side re-order of the ranked queue.
//
// The server (reading_queue → _ranking.rank_explain) sends each scored row's
// `rank_parts` (0..1 terms of the signals it KNOWS) and, per response,
// `rank_coverage` / `rank_fill` / `balanced_weights`. Balanced = the server order
// untouched; every other preset re-sorts here with the comparator below. One
// formula, no server round trip, nothing hidden: only the order changes.
import { readStoredJson } from './safeStorage.js';

export const ORDER_STORAGE_KEY = 'zs:readNextOrder';
export const SIGNALS = ['relevance', 'goals', 'quality', 'prestige'];
export const SIGNAL_LABEL = { relevance: 'Relevance', goals: 'Goals', quality: 'Quality', prestige: 'Prestige' };
export const SLIDER_MAX = 10;
export const SLIDER_STEP = 0.5;

// Weights are on the 0–10 slider scale (shares = weight / sum), so a preset and the
// Custom sliders speak the same language: Relevance 100 % = 10, My goals 30/70 = 3/7.
export const PRESETS = [
  { id: 'balanced', label: 'Balanced' },
  { id: 'relevance', label: 'Relevance', weights: { relevance: 10, goals: 0, quality: 0, prestige: 0 } },
  { id: 'goals', label: 'My goals', weights: { relevance: 3, goals: 7, quality: 0, prestige: 0 } },
  { id: 'quality', label: 'Quality first', weights: { relevance: 3, goals: 2, quality: 5, prestige: 0 } },
  { id: 'custom', label: 'Custom' },
];
export const BALANCED_ORDER = { preset: 'balanced' };

const isWeight = (v) => typeof v === 'number' && Number.isFinite(v) && v >= 0 && v <= SLIDER_MAX;
const isValidOrder = (v) => Boolean(v) && PRESETS.some((p) => p.id === v.preset)
  && (v.preset !== 'custom' || (Boolean(v.weights) && SIGNALS.every((s) => isWeight(v.weights[s]))));

// Unknown / broken stored value → Balanced (never throws, never hides rows).
export function readStoredOrder() {
  return readStoredJson(ORDER_STORAGE_KEY, BALANCED_ORDER, isValidOrder);
}

const snap = (v) => Math.round(v / SLIDER_STEP) * SLIDER_STEP;

// Balanced as shares: the server's actual blend weights (quality is a lift inside
// the ★ band there, not a weight).
function balancedShares(meta) {
  const b = meta?.balancedWeights || { relevance: 0.45, goals: 0.4, prestige: 0.15 };
  return { relevance: b.relevance, goals: b.goals, quality: 0, prestige: b.prestige };
}

// The slider-scale weights an order stands for.
export function orderWeights(order, meta) {
  if (order.preset === 'custom') return order.weights;
  const preset = PRESETS.find((p) => p.id === order.preset)?.weights;
  if (preset) return preset;
  return Object.fromEntries(Object.entries(balancedShares(meta)).map(([s, v]) => [s, snap(v * SLIDER_MAX)]));
}

// Choosing Custom starts from the weights of the order it replaces.
export function selectPreset(prev, preset, meta) {
  if (preset !== 'custom') return { preset };
  return { preset, weights: { ...orderWeights(prev, meta) } };
}

const hasData = (meta, signal) => (meta?.coverage?.[signal]?.present || 0) > 0;

// Share of each signal in the key: weight over the sum of the weights of the
// signals that have at least one value in the cohort (a signal nobody has is dropped).
export function effectiveShares(weights, meta) {
  const live = SIGNALS.filter((s) => hasData(meta, s));
  const total = live.reduce((sum, s) => sum + weights[s], 0);
  return Object.fromEntries(SIGNALS.map((s) => [s, total > 0 && live.includes(s) ? weights[s] / total : 0]));
}

const partOf = (row, signal, meta) => row.rank_parts?.[signal] ?? meta?.fill?.[signal] ?? 0;

// Key of one scored row: Σ share × part; an unknown part takes the server's fill value.
export function presetKey(row, shares, meta) {
  return SIGNALS.reduce((sum, s) => sum + shares[s] * partOf(row, s, meta), 0);
}

// The shares an order puts on the signals, as the state line shows them: Balanced =
// the server's blend weights, any other order = its weights over the signals with data.
export function orderShares(order, meta) {
  return order.preset === 'balanced' ? balancedShares(meta) : effectiveShares(orderWeights(order, meta), meta);
}

// Where the Order control exists at all: a ranked, search-free list whose response
// carries the parts (an old offline snapshot does not).
export function orderAvailable({ sortField, search, modelReady, meta }) {
  return sortField === 'best' && !String(search || '').trim() && Boolean(modelReady) && Boolean(meta?.coverage);
}

// Comparator: group ↑ → scored before unscored → key ↓ → date added ↓ (→ server position).
// Rows outside the ranked cohort — read ones and your own «not interested» (no
// rank_group), shown on request — stay after the unread ones in their server order.
export function sortByPreset(items, order, meta) {
  if (!order || order.preset === 'balanced' || !meta?.coverage) return items;
  const shares = effectiveShares(orderWeights(order, meta), meta);
  const unread = [];
  const read = [];
  items.forEach((row, pos) => {
    if (row.read || typeof row.rank_group !== 'number') { read.push(row); return; }
    const scored = Boolean(row.rank_parts);
    unread.push({ row, pos, scored, group: row.rank_group, key: scored ? presetKey(row, shares, meta) : 0 });
  });
  unread.sort((a, b) => (
    a.group - b.group
    || Number(b.scored) - Number(a.scored)
    || b.key - a.key
    || (b.row.date_added || '').localeCompare(a.row.date_added || '')
    || a.pos - b.pos
  ));
  return [...unread.map((u) => u.row), ...read];
}

// Contributions behind one row's position (Why here): signal, the row's term, its
// share and share × term; their sum is the key. Balanced uses the server's weights.
export function explainRow(row, order, meta) {
  const shares = orderShares(order, meta);
  const rows = SIGNALS.filter((s) => shares[s] > 0).map((s) => {
    const known = row.rank_parts?.[s] !== undefined;
    const part = partOf(row, s, meta);
    return { signal: s, known, part, share: shares[s], contribution: shares[s] * part };
  });
  return { rows, key: rows.reduce((sum, r) => sum + r.contribution, 0) };
}
