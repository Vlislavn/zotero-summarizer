// Pure helpers extracted from Pending.jsx to keep the page file under
// the per-page LOC budget. No React here — only payload parsing, draft
// construction, and presentational utilities for queued Zotero changes.

export const STATUS_TABS = [
  { value: 'pending', label: 'Pending' },
  { value: 'applied', label: 'Applied' },
  { value: 'rejected', label: 'Rejected' },
  { value: 'failed', label: 'Failed' },
];

export function flattenCollections(nodes, depth = 0) {
  const flat = [];
  for (const node of nodes || []) {
    flat.push({
      key: node.key,
      name: node.name,
      item_count: node.item_count || 0,
      depth,
    });
    if (node.children?.length) {
      flat.push(...flattenCollections(node.children, depth + 1));
    }
  }
  return flat;
}

export function stripHtml(input) {
  return String(input || '').replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();
}

export function parseCommaTags(text) {
  return String(text || '')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean);
}

export function readPayload(change) {
  try {
    if (typeof change.payload_json === 'string') {
      return JSON.parse(change.payload_json || '{}');
    }
    return change.payload_json || {};
  } catch {
    return {};
  }
}

export function buildDraft(change) {
  const payload = readPayload(change);
  if (change.change_type === 'tag_changes') {
    return {
      add_tags_text: (payload.add_tags || []).join(', '),
      remove_tags_text: (payload.remove_tags || []).join(', '),
    };
  }
  if (
    change.change_type === 'add_to_collection'
    || change.change_type === 'remove_from_collection'
  ) {
    return {
      collection_key: String(payload.collection_key || '').trim(),
      collection_path: String(payload.collection_path || payload.collection_name || '').trim(),
    };
  }
  if (change.change_type === 'add_note') {
    return {
      note_title: String(payload.note_title || 'Triage note').trim(),
      note_html: String(payload.note_html || '').trim(),
    };
  }
  return {};
}

export function previewChange(change) {
  try {
    const payload = readPayload(change);
    if (change.change_type === 'tag_changes') {
      const added = (payload.add_tags || []).join(', ');
      const removed = (payload.remove_tags || []).join(', ');
      return `add=[${added}] remove=[${removed}]`;
    }
    if (change.change_type === 'add_note') {
      return `${payload.note_title || 'Triage note'} :: ${stripHtml(payload.note_html || '').slice(0, 100)}`;
    }
    if (change.change_type === 'add_to_collection') {
      const target = String(
        payload.collection_path || payload.collection_name || payload.collection_key || '',
      ).trim();
      return `add_to_collection=[${target}]`;
    }
    if (change.change_type === 'remove_from_collection') {
      const target = String(
        payload.collection_path || payload.collection_name || payload.collection_key || '',
      ).trim();
      return `remove_from_collection=[${target}]`;
    }
    return JSON.stringify(payload).slice(0, 140);
  } catch {
    return String(change.payload_json || '');
  }
}

export function changeBadgeClass(type) {
  switch (type) {
    case 'tag_changes':
      return 'bg-amber-100 text-amber-800 border border-amber-300';
    case 'add_to_collection':
      return 'bg-emerald-100 text-emerald-800 border border-emerald-300';
    case 'remove_from_collection':
      return 'bg-rose-100 text-rose-800 border border-rose-300';
    case 'add_note':
      return 'bg-sky-100 text-sky-800 border border-sky-300';
    default:
      return 'bg-slate-100 text-slate-700 border border-slate-300';
  }
}

export function changeTypeLabel(type) {
  return String(type || '').replace(/_/g, ' ');
}

export function collectionOptionLabel(entry) {
  const indent = '— '.repeat(entry.depth || 0);
  return `${indent}${entry.name} (${entry.item_count})`;
}

export function buildPayloadFromDraft(change, draftsForId, flatCollections) {
  const draft = { ...buildDraft(change), ...(draftsForId || {}) };
  if (change.change_type === 'tag_changes') {
    return {
      add_tags: parseCommaTags(draft.add_tags_text || ''),
      remove_tags: parseCommaTags(draft.remove_tags_text || ''),
    };
  }
  if (
    change.change_type === 'add_to_collection'
    || change.change_type === 'remove_from_collection'
  ) {
    const collectionKey = String(draft.collection_key || '').trim();
    const entry = (flatCollections || []).find((n) => String(n.key) === collectionKey);
    const collectionPath = String(entry?.name || draft.collection_path || '').trim();
    return { collection_key: collectionKey, collection_path: collectionPath };
  }
  if (change.change_type === 'add_note') {
    return {
      note_title: String(draft.note_title || '').trim(),
      note_html: String(draft.note_html || '').trim(),
    };
  }
  return null;
}
