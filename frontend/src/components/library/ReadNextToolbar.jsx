import { useState } from 'react';
import BulkVerdictBar from './BulkVerdictBar.jsx';
import { CustomPanel, OrderCaption, OrderControl } from './OrderControls.jsx';
import RankNote from './RankNote.jsx';
import { presetsOffReason } from './readNextCopy.js';
import { readStorage } from '../../utils/safeStorage.js';

// Bulk "Add to collection" (the Meaning-search → Zotero collection shortcut), to the chosen target.
function CollectionTarget({
  collections, target: [targetCollection, setTargetCollection], onAddToCollection, selected, serverAvailable, addingToCollection,
}) {
  const targetValid = collections.some((c) => c.key === targetCollection);
  return (
    <span className="inline-flex items-center gap-1">
      <select
        value={targetValid ? targetCollection : ''}
        onChange={(e) => setTargetCollection(e.target.value)}
        className="max-w-[180px] px-1.5 py-0.5 rounded-md border border-slate-300 text-[11px] text-slate-700 bg-white"
        title="Target Zotero collection for the selected papers"
      >
        <option value="">Collection…</option>
        {collections.map((c) => (
          <option key={c.key} value={c.key}>
            {`${' '.repeat(c.depth * 2)}${c.name}`}
          </option>
        ))}
      </select>
      <button
        type="button"
        onClick={() => onAddToCollection(targetCollection)}
        disabled={!serverAvailable || !selected?.size || !targetValid || addingToCollection}
        className="px-3 py-0.5 rounded-md bg-slate-700 text-[11px] font-semibold text-white hover:bg-slate-800 disabled:bg-slate-300 disabled:text-slate-500"
        title="Add the selected papers to this Zotero collection (backup-first; no more memorizing titles and re-finding them in Zotero)"
      >
        {addingToCollection ? 'Adding…' : `Add to collection (${selected?.size || 0})`}
      </button>
    </span>
  );
}

// What Select mode adds: triage, the bulk verdicts, the collection shortcut.
function SelectActions({
  selected, serverAvailable, starting, onRunTriage, onBulkDone, flow, priorOf, collections, target, onAddToCollection,
  addingToCollection,
}) {
  return (
    <>
      <button
        type="button"
        onClick={onRunTriage}
        disabled={!serverAvailable || !selected || selected.size === 0 || starting}
        className="px-3 py-0.5 rounded-md bg-teal-700 text-[11px] font-semibold text-white hover:bg-teal-800 disabled:bg-slate-300 disabled:text-slate-500"
      >
        {starting ? 'Starting…' : `Run triage (${selected?.size || 0})`}
      </button>
      {onBulkDone && (
        <BulkVerdictBar keys={[...(selected || [])]} disabled={!serverAvailable} onDone={onBulkDone} onVerdicts={flow.batchVerdict} priorOf={priorOf} />
      )}
      {collections.length > 0 && onAddToCollection && (
        <CollectionTarget
          collections={collections}
          target={target}
          onAddToCollection={onAddToCollection}
          selected={selected}
          serverAvailable={serverAvailable}
          addingToCollection={addingToCollection}
        />
      )}
    </>
  );
}

// «Select» and what Select mode adds.
function SelectControls(props) {
  const { selectMode, onSelectClick } = props;
  return (
    <div className="flex flex-wrap items-center gap-3">
      <button
        type="button"
        onClick={onSelectClick}
        className={`px-2 py-0.5 rounded-md text-[11px] font-medium border ${
          selectMode ? 'bg-teal-600 text-white border-teal-600' : 'border-slate-300 text-slate-700 hover:bg-slate-50'
        }`}
        title="Select multiple papers for a bulk action: add them all to a Zotero collection, or send to triage. (To file a single paper, just open it.)"
      >
        {selectMode ? 'Done selecting' : 'Select'}
      </button>
      {selectMode && <SelectActions {...props} />}
    </div>
  );
}

// ONE Order list: presets + Zotero columns (Jakob's Law: Zotero's own field names), then the order note.
function OrderRow(props) {
  const { order, orderMeta, sort, orderOn, phone, offline, modelReady, searchQuery, onOrderChange, onSortChange } = props;
  return (
    <div className="text-xs text-slate-600 flex flex-wrap items-center gap-x-2 gap-y-1">
      {onSortChange && onOrderChange && (
        <OrderControl
          order={order}
          meta={orderMeta}
          sort={sort}
          reason={presetsOffReason({ offline, searching: Boolean(searchQuery.trim()), modelReady, hasMeta: Boolean(orderMeta) })}
          onOrderChange={onOrderChange}
          onSortChange={onSortChange}
          caption={phone && orderOn && sort.field === 'best' ? <OrderCaption order={order} meta={orderMeta} /> : null}
        />
      )}
      <RankNote {...props} />
    </div>
  );
}

// Top bar: the order (presets, Zotero columns, a note on the current order) and Select; the custom weights below it. Takes the
// props of both parts flat.
export default function ReadNextToolbar(props) {
  // Defaults to the last-used collection (validated against the current list) so the routine
  // "send picks to my reading collection" is select → one click.
  const target = useState(() => readStorage('zs:lastCollectionKey') || '');
  const { order, orderMeta, orderOn, onOrderChange } = props;
  return (
    <>
      <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
        <OrderRow {...props} />
        <SelectControls {...props} target={target} />
      </div>
      {orderOn && order.preset === 'custom' && (
        <CustomPanel order={order} meta={orderMeta} onChange={onOrderChange} />
      )}
    </>
  );
}
