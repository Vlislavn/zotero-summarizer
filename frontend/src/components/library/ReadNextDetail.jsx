import { useMemo } from 'react';
import LibraryDetail from './LibraryDetail.jsx';
import { WhyHerePanel } from './WhyHere.jsx';
import { Disclosure } from '../paper/review/primitives.jsx';

// «Why it's here» lives inside the open paper (Order presets only).
function whyHere({ orderOn, order, orderMeta, queueItems }, it) {
  if (!orderOn || !it?.rank_parts) return null;
  return (
    <Disclosure summary="Why it's here">
      <WhyHerePanel
        item={it}
        order={order}
        meta={orderMeta}
        mustCount={queueItems.filter((r) => r.rank_group === 0).length}
      />
    </Disclosure>
  );
}

// The open paper's detail (a Sheet on phones, the right half on wide screens) wired to the list:
// every exit goes through the unsaved-note guard, a saved paper closes and refreshes the page.
export default function ReadNextDetail({
  openState, queueItems, items, loading, err, collections, isDesktop, guard, why, goalHigh, modelReady,
  surface, flow, decideRef, toast, setSelKey, onSaved,
}) {
  const openKey = openState.key;
  const { notesDirty, setNotesDirty, leave, setLeave, leaveNow, guarded } = guard;
  const detailItem = useMemo(
    () => (openKey ? queueItems.find((it) => it.item_key === openKey) || null : null),
    [openKey, queueItems],
  );
  function saveOpen() {
    if (notesDirty) return;
    openState.close();
    onSaved?.();
  }
  function refreshOpen() {
    if (!notesDirty) onSaved?.();
  }
  return (
    <LibraryDetail
      itemKey={openKey}
      item={detailItem}
      sheet={!isDesktop}
      inList={loading || Boolean(err) || items.some((it) => it.item_key === openKey)}
      nav={openState}
      collections={collections}
      onClose={() => guarded(openState.close)}
      onStep={(key) => guarded(() => openState.open(key))}
      leave={leave}
      onStay={() => { setLeave(null); setSelKey(openKey); }}
      onLeave={leaveNow}
      onSaved={saveOpen}
      onQueueRefresh={refreshOpen}
      onNotesDirtyChange={setNotesDirty}
      why={whyHere({ ...why, queueItems }, detailItem)}
      goalsStrong={goalHigh.has(openKey)}
      modelReady={modelReady}
      surface={surface}
      onVerdict={(priority, prior) => flow.verdict(openKey, priority, { prior })}
      caughtUp={flow.caughtUp}
      decideRef={decideRef}
      toast={toast}
    />
  );
}
