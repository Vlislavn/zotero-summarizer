import { describe, it, expect } from 'vitest';
import { sortBorderByUncertainty } from './AnnotationVerdict_helpers.jsx';

const k = (xs) => xs.map((x) => x.item_key);

describe('sortBorderByUncertainty — active-learning order (most decision-worthy first)', () => {
  it('puts model⇄prediction conflicts ahead of plain border picks', () => {
    const items = [
      { item_key: 'plain', flags: ['border'], border_distance: 0.01 },
      { item_key: 'conflict', flags: ['border', 'conflict'], border_distance: 0.5 },
    ];
    expect(k(sortBorderByUncertainty(items))).toEqual(['conflict', 'plain']);
  });

  it('within the same conflict tier, smallest border_distance (least certain) leads', () => {
    const items = [
      { item_key: 'far', flags: ['border'], border_distance: 0.4 },
      { item_key: 'near', flags: ['border'], border_distance: 0.05 },
      { item_key: 'mid', flags: ['border'], border_distance: 0.2 },
    ];
    expect(k(sortBorderByUncertainty(items))).toEqual(['near', 'mid', 'far']);
  });

  it('is stable and tolerates missing distance/flags (no throw, undefined sinks)', () => {
    const items = [
      { item_key: 'a', flags: ['border', 'conflict'], border_distance: 0.3 },
      { item_key: 'b', flags: ['border', 'conflict'], border_distance: 0.3 },
      { item_key: 'c' },
    ];
    const out = k(sortBorderByUncertainty(items));
    expect(out.slice(0, 2)).toEqual(['a', 'b']);  // stable tie order
    expect(out[2]).toBe('c');                       // no distance → last
  });

  it('does not mutate the input array', () => {
    const items = [
      { item_key: 'x', flags: ['border'], border_distance: 0.9 },
      { item_key: 'y', flags: ['border', 'conflict'], border_distance: 0.1 },
    ];
    const before = k(items);
    sortBorderByUncertainty(items);
    expect(k(items)).toEqual(before);
  });
});
