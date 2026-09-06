import { describe, expect, it } from 'vitest';
import { fulltextMessage } from './todayHelpers.js';

describe('fulltextMessage', () => {
  it('reports attachment success separately from missing full text', () => {
    const result = fulltextMessage({ attached: 2, outcomes: [
      { status: 'attached_unpaywall' }, { status: 'attached_cached' }, { status: 'no_oa_source' },
    ] });
    expect(result).toEqual({ text: 'PDFs attached 2; 1 full text unavailable', unavailable: 1 });
  });
});
