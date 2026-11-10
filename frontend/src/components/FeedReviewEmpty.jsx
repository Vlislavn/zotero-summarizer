import { useEffect, useRef, useState } from 'react';
import { refreshRssFeeds } from '../api/settingsApi.js';
import { getTriageStatus, triggerTriageBacklog } from '../api/dailyApi.js';
import { humanizeError } from '../utils/humanizeError.js';

// Empty Feed-review queue: the in-app equivalent of `zotero-summarizer feeds run`
// (fetch new RSS items, then score the un-triaged backlog — the endpoints Settings
// and Today already use). Polls the backlog job and calls onDone() to reload.
export default function FeedReviewEmpty({ onDone }) {
  const [phase, setPhase] = useState('idle'); // idle | fetching | scoring
  const [error, setError] = useState('');
  const alive = useRef(true);
  useEffect(() => () => { alive.current = false; }, []);

  async function run() {
    setError('');
    setPhase('fetching');
    try {
      await refreshRssFeeds({});
      await triggerTriageBacklog();
      setPhase('scoring');
      let status;
      do {
        await new Promise((resolve) => setTimeout(resolve, 3000));
        if (!alive.current) return;
        status = await getTriageStatus();
      } while (status?.running);
      if (status?.error) throw new Error(status.error);
      setPhase('idle');
      onDone();
    } catch (err) {
      if (!alive.current) return;
      setPhase('idle');
      setError(humanizeError(err));
    }
  }

  const busy = phase !== 'idle';
  return (
    <div className="text-sm text-slate-600 space-y-2">
      <p>No items awaiting review. Fetch the newest feed items and score them with the model.</p>
      <button type="button" onClick={run} disabled={busy}
        className="px-3 py-1.5 rounded-lg bg-teal-700 text-white text-sm font-semibold hover:bg-teal-800 disabled:bg-slate-300 disabled:text-slate-500">
        {phase === 'fetching' ? 'Fetching feeds…' : phase === 'scoring' ? 'Scoring new items…' : 'Fetch & score new feed items'}
      </button>
      {error && <p role="alert" className="text-rose-700">{error}</p>}
    </div>
  );
}
