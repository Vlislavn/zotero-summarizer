// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  PRESETS, SIGNALS, explainRow, orderWeights, presetKey, effectiveShares, readStoredOrder,
  selectPreset, sortByPreset, ORDER_STORAGE_KEY,
} from './readNextOrder.js';

// Independent restatement of the requirement formula (Ф4):
// key = Σ(weight × part) / Σ(weights of signals with ≥ 1 value in the cohort).
function formulaKey(parts, weights, coverage, fill) {
  let num = 0;
  let den = 0;
  for (const s of SIGNALS) {
    if (!(coverage[s].present > 0)) continue;
    den += weights[s];
    num += weights[s] * (parts[s] !== undefined ? parts[s] : (fill[s] ?? 0));
  }
  return den > 0 ? num / den : 0;
}

const cov = (of, present) => ({
  relevance: { present: of, of },
  goals: { present: present.goals, of },
  quality: { present: present.quality, of },
  prestige: { present: present.prestige, of },
});

// Four reference cohorts: [rows' parts, meta].
const VECTORS = {
  'no goal for one row': {
    rows: [
      { relevance: 1, goals: 0.8, prestige: 0.2, quality: 0.9 },
      { relevance: 0.5, goals: 0.2, quality: 0.4 },
      { relevance: 0.1, prestige: 1 },
    ],
    meta: { coverage: cov(3, { goals: 2, quality: 2, prestige: 2 }), fill: { goals: 0, prestige: 0.6, quality: 0.65 } },
  },
  'goal for one row only': {
    rows: [{ relevance: 0.3, goals: 1 }, { relevance: 0.9 }, { relevance: 0.6, quality: 0.5 }],
    meta: { coverage: cov(3, { goals: 1, quality: 1, prestige: 0 }), fill: { goals: 0, quality: 0.5 } },
  },
  'no prestige anywhere': {
    rows: [{ relevance: 0.2, goals: 0.7 }, { relevance: 0.8, goals: 0.1 }, { relevance: 0.5, goals: 0.5 }],
    meta: { coverage: cov(3, { goals: 3, quality: 0, prestige: 0 }), fill: { goals: 0 } },
  },
  'all relevance equal': {
    rows: [{ relevance: 0.5, goals: 0 }, { relevance: 0.5, goals: 1 }, { relevance: 0.5, goals: 0.4, prestige: 0.3 }],
    meta: { coverage: cov(3, { goals: 3, quality: 0, prestige: 1 }), fill: { goals: 0, prestige: 0.3 } },
  },
};
const WEIGHTS = {
  relevance: { relevance: 10, goals: 0, quality: 0, prestige: 0 },
  goals: { relevance: 3, goals: 7, quality: 0, prestige: 0 },
  quality: { relevance: 3, goals: 2, quality: 5, prestige: 0 },
  custom: { relevance: 4.5, goals: 4, quality: 0.5, prestige: 1.5 },
};

describe('С4 — client key equals the Ф4 formula (1e-9)', () => {
  for (const [name, { rows, meta }] of Object.entries(VECTORS)) {
    for (const [wname, weights] of Object.entries(WEIGHTS)) {
      it(`${name} · ${wname}`, () => {
        const shares = effectiveShares(weights, meta);
        rows.forEach((parts) => {
          const got = presetKey({ rank_parts: parts }, shares, meta);
          expect(Math.abs(got - formulaKey(parts, weights, meta.coverage, meta.fill))).toBeLessThan(1e-9);
        });
      });
    }
  }

  it('hand-checked: a signal nobody has drops out of the denominator', () => {
    const { rows, meta } = VECTORS['no prestige anywhere'];
    const shares = effectiveShares({ relevance: 4.5, goals: 4, quality: 0, prestige: 1.5 }, meta);
    // (4.5·0.2 + 4·0.7) / (4.5 + 4)
    expect(presetKey({ rank_parts: rows[0] }, shares, meta)).toBeCloseTo(3.7 / 8.5, 9);
  });
});

describe('С5 — Why here contributions add up to the key (±0.001)', () => {
  for (const [name, { rows, meta }] of Object.entries(VECTORS)) {
    const metaWithBalanced = { ...meta, balancedWeights: { relevance: 0.45, goals: 0.4, prestige: 0.15 } };
    for (const order of [{ preset: 'balanced' }, ...Object.entries(WEIGHTS).map(([, weights]) => ({ preset: 'custom', weights }))]) {
      it(`${name} · ${order.preset}`, () => {
        rows.forEach((parts) => {
          const row = { rank_parts: parts };
          const { rows: lines, key } = explainRow(row, order, metaWithBalanced);
          const sum = lines.reduce((a, l) => a + l.contribution, 0);
          expect(Math.abs(sum - key)).toBeLessThan(0.001);
          if (order.preset !== 'balanced') {
            const shares = effectiveShares(order.weights, metaWithBalanced);
            expect(Math.abs(key - presetKey(row, shares, metaWithBalanced))).toBeLessThan(1e-9);
          }
        });
      });
    }
  }
});

describe('sortByPreset', () => {
  const meta = {
    coverage: cov(4, { goals: 4, quality: 0, prestige: 0 }),
    fill: { goals: 0 },
    balancedWeights: { relevance: 0.6, goals: 0.4, prestige: 0 },
  };
  const row = (id, group, rel, goals, date, extra = {}) => ({
    item_key: id, rank_group: group, rank_parts: { relevance: rel, goals }, date_added: date, ...extra,
  });
  const items = [
    row('m', 0, 0.9, 0.1, '2026-01-01'),
    row('a', 1, 0.9, 0.1, '2026-01-02'),
    row('b', 1, 0.1, 0.9, '2026-01-03'),
    { item_key: 'u', rank_group: 1, date_added: '2026-02-01' },
    row('d', 2, 1, 1, '2026-01-05'),
    { item_key: 'r', read: true, date_added: '2026-03-01', rank_parts: { relevance: 1, goals: 1 } },
    { item_key: 'n', date_added: '2026-03-02' },  // your own «not interested»: no rank_group
  ];
  const ids = (list) => list.map((r) => r.item_key);

  it('Balanced returns the server order untouched', () => {
    expect(sortByPreset(items, { preset: 'balanced' }, meta)).toBe(items);
  });

  it('keeps group order, scored before unscored, rows outside the cohort last; hides nothing', () => {
    const out = sortByPreset(items, { preset: 'goals' }, meta);
    expect(ids(out)).toEqual(['m', 'b', 'a', 'u', 'd', 'r', 'n']);
    expect(out).toHaveLength(items.length);
  });

  it('ties go to the newer date added', () => {
    const tied = [row('old', 1, 0.5, 0.5, '2026-01-01'), row('new', 1, 0.5, 0.5, '2026-01-09')];
    expect(ids(sortByPreset(tied, { preset: 'relevance' }, meta))).toEqual(['new', 'old']);
  });

  it('leaves the list alone without server parts', () => {
    expect(sortByPreset(items, { preset: 'goals' }, null)).toBe(items);
  });
});

describe('stored order', () => {
  let saved;
  beforeEach(() => {
    saved = {};
    vi.stubGlobal('localStorage', { getItem: (k) => saved[k] ?? null, setItem: (k, v) => { saved[k] = v; } });
  });
  afterEach(() => vi.unstubAllGlobals());
  const store = (v) => { saved[ORDER_STORAGE_KEY] = typeof v === 'string' ? v : JSON.stringify(v); };

  it('defaults to Balanced and rejects broken values', () => {
    expect(readStoredOrder()).toEqual({ preset: 'balanced' });
    for (const bad of ['{nope', { preset: 'wild' }, { preset: 'custom' }, { preset: 'custom', weights: { relevance: 99, goals: 0, quality: 0, prestige: 0 } }]) {
      store(bad);
      expect(readStoredOrder()).toEqual({ preset: 'balanced' });
    }
  });

  it('keeps a valid choice', () => {
    store({ preset: 'goals' });
    expect(readStoredOrder()).toEqual({ preset: 'goals' });
    const custom = { preset: 'custom', weights: { relevance: 1, goals: 2, quality: 3, prestige: 4 } };
    store(custom);
    expect(readStoredOrder()).toEqual(custom);
  });

  it('Custom starts from the previous order’s weights (Balanced → 45/40/0/15)', () => {
    const meta = { balancedWeights: { relevance: 0.45, goals: 0.4, prestige: 0.15 } };
    expect(selectPreset({ preset: 'balanced' }, 'custom', meta).weights)
      .toEqual({ relevance: 4.5, goals: 4, quality: 0, prestige: 1.5 });
    expect(selectPreset({ preset: 'goals' }, 'custom', meta).weights).toEqual(orderWeights({ preset: 'goals' }, meta));
    expect(PRESETS.map((p) => p.id)).toEqual(['balanced', 'relevance', 'goals', 'quality', 'custom']);
  });
});
