import { describe, it, expect } from 'vitest';
import {
  changeBadgeTone,
  changeBadgeClass,
  changeTypeLabel,
  buildDraft,
  buildPayloadFromDraft,
  previewChange,
  parseCommaTags,
  collectionOptionLabel,
} from './pendingHelpers.js';

// changeBadgeTone() is the new mapping the Pending page uses to render the
// change pill through the shared <ActionBadge tone=…> primitive. It must keep
// the exact palette the old bespoke changeBadgeClass() encoded, so the badge
// looks byte-identical after the swap.
describe('changeBadgeTone', () => {
  const cases = [
    ['tag_changes', 'amber'],
    ['add_to_collection', 'emerald'],
    ['remove_from_collection', 'rose'],
    ['add_note', 'sky'],
  ];

  it.each(cases)('maps %s -> %s', (type, tone) => {
    expect(changeBadgeTone(type)).toBe(tone);
  });

  it('falls back to slate for unknown / empty types', () => {
    expect(changeBadgeTone('something_else')).toBe('slate');
    expect(changeBadgeTone('')).toBe('slate');
    expect(changeBadgeTone(undefined)).toBe('slate');
  });

  it('agrees with the legacy changeBadgeClass color for every known type', () => {
    // The tone token must select the same base color family the old raw class
    // used (e.g. amber tone <-> amber-100 class), so the migration is a no-op
    // visually.
    for (const [type, tone] of cases) {
      expect(changeBadgeClass(type)).toContain(`${tone}-100`);
    }
    // Unknown type -> slate in both representations.
    expect(changeBadgeTone('zzz')).toBe('slate');
    expect(changeBadgeClass('zzz')).toContain('slate-100');
  });
});

// Light regression coverage for the helpers the page leans on, so a refactor
// that breaks draft/payload round-tripping fails loudly.
describe('draft / payload round-trip', () => {
  it('round-trips tag_changes through buildDraft -> buildPayloadFromDraft', () => {
    const change = {
      change_type: 'tag_changes',
      payload_json: { add_tags: ['a', 'b'], remove_tags: ['c'] },
    };
    const draft = buildDraft(change);
    expect(draft.add_tags_text).toBe('a, b');
    expect(draft.remove_tags_text).toBe('c');
    const payload = buildPayloadFromDraft(change, draft, []);
    expect(payload).toEqual({ add_tags: ['a', 'b'], remove_tags: ['c'] });
  });

  it('resolves the collection path from the flat list for collection edits', () => {
    const change = {
      change_type: 'add_to_collection',
      payload_json: { collection_key: 'K1' },
    };
    const flat = [{ key: 'K1', name: 'Read Next', depth: 0, item_count: 3 }];
    const payload = buildPayloadFromDraft(change, { collection_key: 'K1' }, flat);
    expect(payload).toEqual({ collection_key: 'K1', collection_path: 'Read Next' });
  });

  it('returns null for a non-editable change type', () => {
    expect(buildPayloadFromDraft({ change_type: 'mystery' }, {}, [])).toBeNull();
  });
});

describe('presentational helpers', () => {
  it('humanizes change_type labels', () => {
    expect(changeTypeLabel('add_to_collection')).toBe('add to collection');
    expect(changeTypeLabel(undefined)).toBe('');
  });

  it('parses comma-separated tags, trimming and dropping blanks', () => {
    expect(parseCommaTags(' a , b ,, c ')).toEqual(['a', 'b', 'c']);
    expect(parseCommaTags('')).toEqual([]);
  });

  it('indents nested collection option labels', () => {
    expect(collectionOptionLabel({ name: 'Top', item_count: 2, depth: 0 })).toBe('Top (2)');
    expect(collectionOptionLabel({ name: 'Child', item_count: 1, depth: 2 })).toBe('— — Child (1)');
  });

  it('previews a tag change compactly', () => {
    const preview = previewChange({
      change_type: 'tag_changes',
      payload_json: { add_tags: ['x'], remove_tags: ['y'] },
    });
    expect(preview).toBe('add=[x] remove=[y]');
  });
});
