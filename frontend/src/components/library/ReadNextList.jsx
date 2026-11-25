import ReadNextRow from './ReadNextRow.jsx';
import { GroupAction, GroupHeader } from './GroupParts.jsx';
import Skeleton from '../ui/Skeleton.jsx';
import { REVEAL_STEP } from './readNextModel.js';

const KEY_HINT = 'j / k move · 1–4 verdict · z undo · Enter open · Esc close · / search';

// Above the list: the loading skeleton (nothing to show yet) and the Undo toast, which sticks while you scroll.
export function ListNotices({ loading, toast }) {
  return (
    <>
      {loading && (
        <div role="status" aria-busy="true"><span className="sr-only">Loading your reading list…</span><Skeleton count={3} height="h-16" /></div>
      )}
      {toast && <div className="sticky top-2 z-20 mb-2">{toast}</div>}
    </>
  );
}

function Rows({ list, ownChip, view }) {
  const { selKey, flashKey, openKey, goalHigh, modelReady, selectMode, selected, onToggleOpen, onToggleSelect } = view;
  return (
    <ol className="space-y-2">
      {list.map((it) => (
        <li
          key={it.item_key}
          data-row-key={it.item_key}
          aria-current={selKey === it.item_key || undefined}
          className={`rounded-xl ${selKey === it.item_key ? 'outline outline-2 outline-teal-600' : ''} ${flashKey === it.item_key ? 'ring-2 ring-teal-300' : ''}`}
        >
          <ReadNextRow
            item={it}
            expanded={openKey === it.item_key}
            ownChip={ownChip}
            goalsStrong={goalHigh.has(it.item_key)}
            modelReady={modelReady}
            selectMode={selectMode}
            checked={selectMode && (selected?.has(it.item_key) || false)}
            onToggleOpen={onToggleOpen}
            onToggleSelect={onToggleSelect}
          />
        </li>
      ))}
    </ol>
  );
}

function MustSection({ must, reveal, mustPreview, count, view }) {
  const { mustAll, setMustAll } = reveal;
  return (
    <section aria-label="Your must read">
      <GroupHeader
        action={must.length > mustPreview && (
          <GroupAction expanded={mustAll} onClick={() => setMustAll((v) => !v)}>
            {mustAll ? 'Show fewer' : `Show all${view.offline ? '' : ` ${must.length}`}`}
          </GroupAction>
        )}
      >
        Your must read{count(must.length)}
      </GroupHeader>
      <Rows list={mustAll ? must : must.slice(0, mustPreview)} ownChip={false} view={view} />
    </section>
  );
}

function DecidedSection({ decided, reveal, count, view }) {
  const { decidedOpen, setDecidedOpen } = reveal;
  return (
    <section aria-label="Decided by you" className="mt-3">
      <GroupAction expanded={decidedOpen} onClick={() => setDecidedOpen((v) => !v)}>
        Decided by you{count(decided.length)} · {decidedOpen ? 'Hide' : 'Show'}
      </GroupAction>
      {decidedOpen && <div className="mt-2"><Rows list={decided} ownChip view={view} /></div>}
    </section>
  );
}

function ShowMore({ shown, pagedTotal, setVisibleCount }) {
  return (
    <div className="mt-3 flex items-center justify-center gap-3 text-xs text-slate-500">
      <span>Showing {shown} of {pagedTotal}</span>
      <button
        type="button"
        onClick={() => setVisibleCount((v) => v + REVEAL_STEP * 4)}
        className="px-3 py-1 rounded-md border border-slate-300 text-slate-700 hover:bg-slate-50"
      >
        Show more
      </button>
    </div>
  );
}

// The ranked rows: by default three groups (your own Must read · To decide · Decided by you), else one flat list.
// `view` carries what a row needs (outline, highlight, tap handlers); `offline` drops the live counts.
export default function ReadNextList({ groups, shown, pagedTotal, reveal, mustPreview, isDesktop, view }) {
  // Counts in the headers are a live answer; the offline copy shows none.
  const count = (n) => (view.offline ? '' : ` · ${n}`);
  return (
    <>
      {groups && groups.must.length > 0 && (
        <MustSection must={groups.must} reveal={reveal} mustPreview={mustPreview} count={count} view={view} />
      )}
      {groups && <GroupHeader>To decide{count(groups.todo.length)}</GroupHeader>}
      <Rows list={shown} ownChip view={view} />
      {pagedTotal > shown.length && (
        <ShowMore shown={shown.length} pagedTotal={pagedTotal} setVisibleCount={reveal.setVisibleCount} />
      )}
      {groups && groups.decided.length > 0 && (
        <DecidedSection decided={groups.decided} reveal={reveal} count={count} view={view} />
      )}
      {isDesktop && shown.length > 0 && (
        <p data-testid="key-hint" className="sticky bottom-0 z-10 mt-3 bg-white py-1.5 text-[11px] text-slate-500">{KEY_HINT}</p>
      )}
    </>
  );
}
