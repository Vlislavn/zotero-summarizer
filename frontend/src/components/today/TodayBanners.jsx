import { Link } from 'react-router-dom';
import { ErrorBanner, StatusBanner } from '../library/shared.jsx';

export function RenewalNote({ result, renewing }) {
  if (!result || renewing) return null;
  return <p role="status" className="mb-3 text-xs text-slate-600">
    {result.added > 0
      ? `New shortlist: ${result.added} new ${result.added === 1 ? 'paper' : 'papers'}`
      : `Same papers — nothing new${result.since ? ` since ${result.since}` : ''}`}
  </p>;
}

export function ErrorBanners({ view, triage }) {
  const { status } = triage;
  const lastRunError = !triage.unknown && !triage.running && (status?.error || status?.rescore_error);
  return <>
    <ErrorBanner error={view.selectionError} title="Selection failed — existing shortlist kept" />
    <ErrorBanner error={view.statusError} title="Member status unavailable — shortlist kept" />
    <ErrorBanner error={view.actionError} title="Action response unavailable — check each paper" />
    <ErrorBanner error={triage.mutation.error} title="Triage backlog unavailable" />
    <ErrorBanner error={triage.query.error} title="Triage status unavailable — reconnecting" />
    {lastRunError && <ErrorBanner error={status.error || status.rescore_error} title="Last triage run failed" />}
  </>;
}

export function StatusBanners({ view }) {
  return <>
    {view.persistenceFailed && <StatusBanner tone="warn" message="Browser storage is unavailable. This shortlist stays open here but may not survive reload. The shortlist itself never resends actions." />}
    {view.visit?.invalid && <StatusBanner tone="warn" message="The saved shortlist could not be read. New shortlist explicitly replaces it; no saved action is replayed." />}
    {view.inFlightCount > 0 && <StatusBanner tone="warn" message={`${view.inFlightCount} ${view.inFlightCount === 1 ? 'paper is' : 'papers are'} still being added. New shortlist is available once they finish.`} />}
    {view.parkedCount > 0 && <StatusBanner tone="warn" message={<>
      {`${view.parkedCount} ${view.parkedCount === 1 ? 'paper waits' : 'papers wait'} for Zotero. Close Zotero, then apply them in Ops › Feed review.`}
      {' '}<Link to="/ops?tab=review" className="inline-flex min-h-11 items-center underline underline-offset-2">Apply approved → Zotero</Link>
    </>} />}
  </>;
}
