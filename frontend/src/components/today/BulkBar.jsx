import CollectionPicker from '../CollectionPicker.jsx';

// Bulk action bar: appears ONLY when ≥ 1 card is ticked (#37/T05, F04). One slim
// bottom row, not a 210 px always-on toolbar covering the cards.
export default function BulkBar({ picked, collectionKey, onCollection, busy, llmEnabled, onCommit, onClear }) {
  const { selected, selectedReady } = picked;
  return <div className="sticky bottom-2 z-10 mb-3 flex flex-wrap items-center gap-2 rounded-xl border border-slate-200 bg-white p-2">
    <span className="text-xs font-medium text-slate-700">{selected.length} selected</span>
    <button type="button" onClick={onClear} className="min-h-11 text-xs text-slate-500 underline">Clear</button>
    <div className="flex-1" />
    <CollectionPicker value={collectionKey} onChange={onCollection} disabled={busy} />
    <button type="button" onClick={() => onCommit('add')} disabled={!selectedReady.length || busy}
      className="min-h-11 rounded-lg bg-forest-800 px-3 py-1.5 text-xs font-medium text-white hover:bg-forest-700 disabled:opacity-40">
      Add to library
    </button>
    <button type="button" onClick={() => onCommit('trash')} disabled={busy}
      aria-label={`Not interested (${selected.length} selected)`}
      className="min-h-11 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-rose-700 hover:border-rose-500 disabled:opacity-40">
      Not interested
    </button>
    {selected.length > selectedReady.length && <span className="basis-full text-xs text-amber-800">
      {llmEnabled ? 'Papers without a review are skipped by Add — Not interested still works.' : 'Enable AI in Settings before adding unreviewed papers.'}
    </span>}
  </div>;
}
