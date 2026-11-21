import { useMemo, useRef } from 'react';
import ReadNextList, { ListNotices } from './ReadNextList.jsx';
import ReadNextDetail from './ReadNextDetail.jsx';
import ReadNextToolbar from './ReadNextToolbar.jsx';
import UndoToast from './UndoToast.jsx';
import useLibraryVerdict from '../../hooks/useLibraryVerdict.js';
import { goalHighKeys } from '../../utils/relevanceBands.js';
import useMediaQuery from './useMediaQuery.js';
import useLeaveGuard from './useLeaveGuard.js';
import useRowSelection from './useRowSelection.js';
import useRowKeys from './useRowKeys.js';
import useRevealRow, { useListReveal } from './useRevealRow.js';
import { listLayout, mustPreviewFor, priorOf, withDefaults } from './readNextModel.js';

// Stage-2 "Read next": the single Library surface. Ranked queue over the WHOLE
// library; a tap opens the paper's detail (LibraryDetail: a Sheet on phones, the right half
// on wide screens) over the still-mounted list. The open paper is `openState`
// (hooks/useOpenPaper, owned by the page so it survives a server-filter remount).
// Read/handled items are hidden unless toggled. An opt-in "Select" mode reveals
// checkboxes for bulk triage (the merged Browse/triage flow), kept secondary.
// The pieces: ReadNextToolbar (order + Select) · ReadNextList (the groups and rows) · ReadNextDetail
// (the open paper); the hooks useLeaveGuard, useRowSelection, useRowKeys and useRevealRow own the behaviour.
const DESKTOP_QUERY = '(min-width: 1024px)';
const PHONE_QUERY = '(max-width: 639px)';  // Tailwind's `sm` starts at 640

export default function ReadNextView(props) {
  const all = withDefaults(props);
  const {
    items, queueItems, loading, err, onSaved, modelReady, selectMode, onToggleSelectMode, selected, onToggleItem,
    collections, offline, filterSig, semantic, searchQuery, sort, order, onOrderChange, orderMeta, cohortFull,
    openState, onRowsChange, onWrite, onFocusSearch,
  } = all;
  const openKey = openState.key;
  const orderOn = Boolean(orderMeta && onOrderChange && sort.field === 'best');  // a Zotero field replaces the preset
  const listRef = useRef(null);
  const isDesktop = useMediaQuery(DESKTOP_QUERY);
  const phone = useMediaQuery(PHONE_QUERY);
  const mustPreview = mustPreviewFor(phone);
  const reveal = useListReveal(filterSig);
  const { groups, shown, pagedTotal } = listLayout({ items, sort, semantic, searchQuery, visibleCount: reveal.visibleCount });
  const guard = useLeaveGuard(openKey);
  const { notesDirty, guarded } = guard;
  const surface = isDesktop ? 'library_split' : 'library_sheet';
  const flow = useLibraryVerdict({ queueItems, items, openState, notesDirty, guarded, surface, onRowsChange, onWrite });
  // The toast rides inside the phone Sheet (it silences the page behind it); otherwise it sticks above the list.
  const toast = flow.toast && (
    <UndoToast toast={flow.toast} onUndo={flow.undo} onReason={flow.toggleReason} onDismiss={flow.dismiss} />
  );
  const toastInSheet = Boolean(openKey && !isDesktop);
  const split = Boolean(openKey && isDesktop);  // the list and the detail side by side
  const sel = useRowSelection({ listRef, openKey, openState, isDesktop, guarded });
  const { decideRef, toggleRow } = useRowKeys({
    sel, openKey, openState, isDesktop, guarded, flow, queueItems, items, selectMode, onToggleItem, onFocusSearch,
  });
  const flashKey = useRevealRow({ listRef, openKey, items, groups, mustPreview, reveal });
  const goalHigh = useMemo(
    () => (cohortFull && modelReady && !offline && !searchQuery.trim() ? goalHighKeys(queueItems) : new Set()),
    [cohortFull, modelReady, offline, searchQuery, queueItems],
  );
  const closeAndSelect = () => { openState.close(); onToggleSelectMode(); };
  const onSelectClick = () => (openKey && !selectMode ? guarded(closeAndSelect) : onToggleSelectMode());
  const rowView = {
    selKey: sel.selKey, flashKey, openKey, goalHigh, modelReady, selectMode, selected, offline,
    onToggleOpen: toggleRow, onToggleSelect: onToggleItem,
  };

  return (
    <div>
      <ReadNextToolbar
        {...all}
        orderOn={orderOn}
        phone={phone}
        shownCount={items.length}
        onSelectClick={onSelectClick}
        flow={flow}
        priorOf={(key) => priorOf(queueItems, key)}
      />
      <ListNotices loading={loading && items.length === 0} toast={toastInSheet ? null : toast} />
      <div ref={listRef} className={split ? 'lg:flex lg:gap-3 lg:items-start' : ''}>
      <div className={split ? 'lg:flex-1 lg:min-w-0' : ''}>
        <ReadNextList
          groups={groups}
          shown={shown}
          pagedTotal={pagedTotal}
          reveal={reveal}
          mustPreview={mustPreview}
          isDesktop={isDesktop}
          view={rowView}
        />
      </div>
      {openKey && (
        <ReadNextDetail
          openState={openState}
          queueItems={queueItems}
          items={items}
          loading={loading}
          err={err}
          collections={collections}
          isDesktop={isDesktop}
          guard={guard}
          why={{ orderOn, order, orderMeta }}
          goalHigh={goalHigh}
          modelReady={modelReady}
          surface={surface}
          flow={flow}
          decideRef={decideRef}
          toast={toastInSheet ? toast : null}
          setSelKey={sel.setSelKey}
          onSaved={onSaved}
        />
      )}
      </div>
    </div>
  );
}
