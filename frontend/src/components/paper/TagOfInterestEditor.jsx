import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { updateItemTags, fetchTags } from '../../api/libraryApi.js';
import { isMachineTag } from '../../utils/tags.js';
import TagsRow from '../TagsRow.jsx';
import useServerAvailable from '../../hooks/useServerAvailable.js';

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
export default function TagOfInterestEditor(props) {
  return <TagEditor key={props.itemKey} {...props} />;
}

function TagEditor({ itemKey, tags = [], onChanged }) {
  const serverAvailable = useServerAvailable();
  const [tagText, setTagText] = useState('');
  const [error, setError] = useState(null);
  const [busyTag, setBusyTag] = useState(null);
  const [deliveryBlocked, setDeliveryBlocked] = useState(false);
  const pending = useRef(null);
  useEffect(() => () => { pending.current = null; }, []);
  const writesBlocked = busyTag !== null || deliveryBlocked;
  // Shared query key → react-query dedupes with Library/Annotate (no extra call).
  const tagsQuery = useQuery({
    queryKey: ['zotero-tags', 300], queryFn: () => fetchTags({ limit: 300 }), staleTime: 5 * 60_000,
    enabled: serverAvailable,
  });
  // Hide the app's own machine tags from both the autocomplete and the chips
  // (R5: every option/chip must have a function) — see utils/tags.js.
  const vocab = (tagsQuery.data?.items || []).filter((t) => t.tag && !isMachineTag(t.tag));
  const shownTags = tags.filter((t) => t && !isMachineTag(t));
  const listId = `tagvocab-${itemKey}`;

  async function applyTags({ addTags = [], removeTags = [] }, busy = '', submittedText = null) {
    if (!serverAvailable || pending.current || deliveryBlocked) return;
    const request = {};
    pending.current = request;
    const current = () => pending.current === request;
    setError(null);
    setBusyTag(busy);
    try {
      let data = await updateItemTags(itemKey, { addTags, removeTags, force: false });
      if (!current()) return;
      if (data?.requires_force) {
        if (!window.confirm('Zotero appears to be running. Change tags anyway? (a backup is taken first)')) return;
        data = await updateItemTags(itemKey, { addTags, removeTags, force: true });
        if (!current()) return;
        if (data?.requires_force) {
          setError('Tag changes were not applied.');
          return;
        }
      }
      if (data?.item_key !== itemKey || !(data.delivery_status === 'applied'
          || (data.delivery_status == null && data.updated === 0))) {
        setDeliveryBlocked(true);
        setError('Tag delivery was not confirmed.');
        return;
      }
      if (submittedText !== null) setTagText((previous) => previous === submittedText ? '' : previous);
    } catch (e) {
      if (!current()) return;
      const delivery = e.body?.details?.delivery_status;
      const notDispatched = delivery === 'failed' || (!delivery
        && ([403, 404, 422].includes(e.status) || e.body?.error === 'zotero_unavailable'));
      setDeliveryBlocked(!notDispatched);
      setError(`Tagging failed: ${e.message || e}`);
      return;
    } finally {
      if (current()) { pending.current = null; setBusyTag(null); }
    }
    onChanged?.();
  }

  async function handleAddFreeTag(e) {
    e.preventDefault();
    const t = tagText.trim();
    if (!t) return;
    await applyTags({ addTags: [t] }, '', tagText);
  }

  if (!serverAvailable) return <div><TagsRow tags={shownTags} /><p className="text-xs text-slate-500">Tag changes need the Mac.</p></div>;

  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-center gap-1.5">
        {TAG_CHIPS.map((c) => (
          <button
            key={c.emoji}
            type="button"
            disabled={writesBlocked}
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
            aria-label="Tag to add"
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
          <button type="submit" disabled={!tagText.trim() || writesBlocked}
            className="px-2 py-0.5 rounded-lg bg-slate-200 text-slate-700 text-xs hover:bg-slate-300 disabled:opacity-50">
            Add
          </button>
        </form>
      </div>
      {shownTags.length > 0 && (
        <fieldset disabled={writesBlocked} className="min-w-0">
          <TagsRow
            tags={shownTags}
            onRemove={(raw) => applyTags({ removeTags: [raw] }, raw)}
            removingKey={busyTag}
          />
        </fieldset>
      )}
      {error && <div role="status" className="text-[11px] text-rose-700">{error}</div>}
      {deliveryBlocked && <p className="text-xs text-slate-600">Tag writes paused — check Zotero and the retained native history/backup before making another change. No automatic retry.</p>}
    </div>
  );
}
