import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { updateItemTags, fetchTags } from '../../api/libraryApi.js';
import { isMachineTag } from '../../utils/tags.js';
import TagsRow from '../TagsRow.jsx';

// Quick "tags of interest" — emoji signals from services/emoji_signals.py that
// also train the gate. Kept short (Hick's Law); free text covers the rest.
const TAG_CHIPS = [
  { emoji: '👀', label: 'skimmed' },
  { emoji: '🧪', label: 'method' },
  { emoji: '🧠', label: 'distilled' },
  { emoji: '💡', label: 'idea' },
  { emoji: '🧱', label: 'limitation' },
];

// Apply tags-of-interest to a paper (writes straight to Zotero) + show current
// tags. Shared by Library inline panel and Annotate detail. The free-text input
// autocompletes from the user's EXISTING Zotero tag vocabulary via a native
// <datalist> (so "change tag to any I have" is a pick, not a retype — Postel /
// Jakob), and current tags are removable chips so "change" covers remove-and-set.
// `onChanged` lets the caller refresh its own queries after a write (engagement
// emoji marks the paper "handled", so the Library queue drops it).
export default function TagOfInterestEditor({ itemKey, tags = [], onChanged }) {
  const [tagText, setTagText] = useState('');
  const [error, setError] = useState(null);
  const [busyTag, setBusyTag] = useState('');
  // Shared query key → react-query dedupes with Library/Annotate (no extra call).
  const tagsQuery = useQuery({
    queryKey: ['zotero-tags', 300], queryFn: () => fetchTags({ limit: 300 }), staleTime: 5 * 60_000,
  });
  // Hide the app's own machine tags from both the autocomplete and the chips
  // (R5: every option/chip must have a function) — see utils/tags.js.
  const vocab = (tagsQuery.data?.items || []).filter((t) => t.tag && !isMachineTag(t.tag));
  const shownTags = tags.filter((t) => t && !isMachineTag(t));
  const listId = `tagvocab-${itemKey}`;

  async function applyTags({ addTags = [], removeTags = [] }, busy = '') {
    setError(null);
    setBusyTag(busy);
    try {
      await updateItemTags(itemKey, { addTags, removeTags });
      onChanged?.();
    } catch (e) {
      setError(`Tagging failed: ${e.message || e}`);
    } finally {
      setBusyTag('');
    }
  }

  async function handleAddFreeTag(e) {
    e.preventDefault();
    const t = tagText.trim();
    if (!t) return;
    setTagText('');
    await applyTags({ addTags: [t] });
  }

  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-center gap-1.5">
        {TAG_CHIPS.map((c) => (
          <button
            key={c.emoji}
            type="button"
            onClick={() => applyTags({ addTags: [`${c.emoji} ${c.label}`] })}
            className="px-2 py-0.5 rounded-full bg-amber-50 text-amber-900 text-xs border border-amber-200 font-medium hover:bg-amber-100"
            title={`Tag this paper "${c.emoji} ${c.label}" in Zotero`}
          >
            {c.emoji} {c.label}
          </button>
        ))}
        <form onSubmit={handleAddFreeTag} className="flex items-center gap-1">
          <input
            type="text"
            list={listId}
            value={tagText}
            onChange={(e) => setTagText(e.target.value)}
            placeholder="tag… (pick or type)"
            className="w-36 px-2 py-0.5 rounded-lg border border-slate-300 text-xs focus:outline-none focus:ring-1 focus:ring-teal-500"
          />
          <datalist id={listId}>
            {vocab.map((t) => (
              <option key={t.tag} value={t.tag}>{t.item_count != null ? `${t.item_count}×` : ''}</option>
            ))}
          </datalist>
          <button type="submit" disabled={!tagText.trim()}
            className="px-2 py-0.5 rounded-lg bg-slate-200 text-slate-700 text-xs hover:bg-slate-300 disabled:opacity-50">
            Add
          </button>
        </form>
      </div>
      {shownTags.length > 0 && (
        <TagsRow
          tags={shownTags}
          onRemove={(raw) => applyTags({ removeTags: [raw] }, raw)}
          removingKey={busyTag}
        />
      )}
      {error && <div className="text-[11px] text-rose-700">{error}</div>}
    </div>
  );
}
