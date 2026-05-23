import { useState } from 'react';
import { updateItemTags } from '../../api/libraryApi.js';
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
// tags. Shared by Library inline panel and Annotate detail. `onChanged` lets the
// caller refresh its own queries after a write (engagement emoji marks the paper
// "handled", so the Library queue drops it; free text just updates the display).
export default function TagOfInterestEditor({ itemKey, tags = [], onChanged }) {
  const [tagText, setTagText] = useState('');
  const [error, setError] = useState(null);

  async function applyTags(addTags) {
    setError(null);
    try {
      await updateItemTags(itemKey, { addTags });
      onChanged?.();
    } catch (e) {
      setError(`Tagging failed: ${e.message || e}`);
    }
  }

  async function handleAddFreeTag(e) {
    e.preventDefault();
    const t = tagText.trim();
    if (!t) return;
    setTagText('');
    await applyTags([t]);
  }

  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-center gap-1.5">
        {TAG_CHIPS.map((c) => (
          <button
            key={c.emoji}
            type="button"
            onClick={() => applyTags([`${c.emoji} ${c.label}`])}
            className="px-2 py-0.5 rounded-full bg-amber-50 text-amber-900 text-xs border border-amber-200 font-medium hover:bg-amber-100"
            title={`Tag this paper "${c.emoji} ${c.label}" in Zotero`}
          >
            {c.emoji} {c.label}
          </button>
        ))}
        <form onSubmit={handleAddFreeTag} className="flex items-center gap-1">
          <input
            type="text"
            value={tagText}
            onChange={(e) => setTagText(e.target.value)}
            placeholder="custom tag…"
            className="w-28 px-2 py-0.5 rounded-lg border border-slate-300 text-xs focus:outline-none focus:ring-1 focus:ring-teal-500"
          />
          <button type="submit" disabled={!tagText.trim()}
            className="px-2 py-0.5 rounded-lg bg-slate-200 text-slate-700 text-xs hover:bg-slate-300 disabled:opacity-50">
            Add
          </button>
        </form>
      </div>
      {tags?.length > 0 && <TagsRow tags={tags} />}
      {error && <div className="text-[11px] text-rose-700">{error}</div>}
    </div>
  );
}
