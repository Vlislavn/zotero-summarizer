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
  approvalPreviews,
  hasApprovalPreview,
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

describe('displayed common approval snapshots', () => {
  const row = {
    id: 7, item_key: 'P7', change_type: 'add_note',
    approval_preview: {
      version: 1, change_id: 7, item_key: 'P7', change_type: 'add_note', payload: { note_html: '<p>Full</p>' },
      edit_revision: 2, target: { database_path: '/owned/zotero.sqlite', device: 1, inode: 2, library_id: 1, library_type: 'user' },
      generated_preview_sha256: null, preview_sha256: 'a'.repeat(64),
    },
  };

  it('forwards only the presented common hashes for an ordinary/generated selection', () => {
    const generated = {
      ...row, id: 8,
      generated_preview: { target: row.approval_preview.target, preview_sha256: 'b'.repeat(64) },
      approval_preview: { ...row.approval_preview, change_id: 8, generated_preview_sha256: 'b'.repeat(64), preview_sha256: 'c'.repeat(64) },
    };
    expect(approvalPreviews([row, generated])).toEqual({ 7: 'a'.repeat(64), 8: 'c'.repeat(64) });
    expect(generated.generated_preview.preview_sha256).toBe('b'.repeat(64));
  });

  it.each([
    null, {}, { version: 2 }, { change_id: 8 }, { item_key: 'OTHER' }, { change_type: 'tag_changes' },
    { target: null }, { target: [] }, { target: 'unknown' },
    { edit_revision: -1 }, { edit_revision: 0.5 }, { payload: null }, { payload: [] },
    { preview_sha256: 'A'.repeat(64) }, { preview_sha256: 'a'.repeat(63) }, { preview_sha256: ['a'.repeat(64)] },
  ])('refuses an unavailable or malformed common snapshot: %j', (override) => {
    const invalid = { ...row, approval_preview: override === null ? null : { ...row.approval_preview, ...override } };
    if (override && Object.keys(override).length === 0) invalid.approval_preview = {};
    expect(hasApprovalPreview(invalid)).toBe(false);
    expect(approvalPreviews([row, invalid])).toBeNull();
  });

  it('never falls back to an inner generated hash or binds a NULL generated target', () => {
    expect(approvalPreviews([{ ...row, approval_preview: null, generated_preview: { preview_sha256: 'b'.repeat(64) } }])).toBeNull();
    expect(approvalPreviews([{ ...row, generated_preview: { target: null } }])).toBeNull();
    expect(approvalPreviews([])).toBeNull();
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
