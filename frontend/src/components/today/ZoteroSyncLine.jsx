// One read-only line under the Today header: how many papers Zotero has not yet
// uploaded to its cloud (they are missing on the iPad until it does). Not the
// app's own pending Zotero exports. Hidden at 0 and on any error — never a toast.
import { useEffect, useRef } from 'react';
import { useQuery } from '@tanstack/react-query';
import { fetchZoteroStatus } from '../../api/libraryApi.js';

export default function ZoteroSyncLine({ enabled, busy }) {
  const { data, refetch } = useQuery({
    queryKey: ['zotero-sync-count'], queryFn: fetchZoteroStatus, enabled, retry: false,
  });
  const wasBusy = useRef(false);
  useEffect(() => {
    // An Add just finished: the count changed, so read it again.
    if (wasBusy.current && !busy) refetch();
    wasBusy.current = busy;
  }, [busy, refetch]);
  const count = data?.stats?.unsynced_items;
  if (!Number.isInteger(count) || count <= 0) return null;
  return <p role="status" data-testid="zotero-sync-line" className="mt-1 text-xs text-slate-600">
    {count} {count === 1 ? 'paper' : 'papers'} waiting for Zotero sync
  </p>;
}
