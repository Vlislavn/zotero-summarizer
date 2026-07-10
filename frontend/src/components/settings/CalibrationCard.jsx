// Settings → "Calibrate to my setup". One button that runs the per-user calibration
// (Tier-1 env probe + Tier-2 text-budget sweep) against the deep-review endpoint and
// saves data/calibration.json. Tesler's Law: the human clicks once; the system measures
// and owns the knobs — no sliders. The sweep runs on a background thread and the card
// POLLS for live "reviewed N of M" progress (the work is bounded but slow — real LLM
// digests). Memory-safe: a remote endpoint loads no local model; a local one is
// memory-pre-flighted server-side.

import { useEffect, useRef, useState } from 'react';
import { calibrateSetup, fetchCalibrateStatus } from '../../api/settingsApi.js';
import { humanizeError } from '../../utils/humanizeError.js';
import { Banner, SectionCard } from '../form/Fields.jsx';
import Button from '../ui/Button.jsx';

const POLL_MS = 2500;

export default function CalibrationCard() {
  const [job, setJob] = useState(null);   // { status, completed, total, phase, result, error }
  const [error, setError] = useState('');
  const pollRef = useRef(null);

  useEffect(() => () => clearTimeout(pollRef.current), []);  // stop polling on unmount

  function poll() {
    pollRef.current = setTimeout(async () => {
      try {
        const s = await fetchCalibrateStatus();
        setJob(s);
        if (s.status === 'running') poll();
      } catch (e) {
        setError(humanizeError(e));
      }
    }, POLL_MS);
  }

  async function run() {
    setError('');
    try {
      setJob(await calibrateSetup({ papers: 3 }));  // returns the initial running status
      poll();
    } catch (e) {
      setError(humanizeError(e));
    }
  }

  const running = job?.status === 'running';
  const result = job?.status === 'ready' ? job.result : null;
  const t2 = result?.tier2;
  const jobError = job?.status === 'error' ? job.error || '' : '';
  const total = job?.total || 0;
  const progressLabel = running
    ? (total ? `Calibrating… reviewed ${job.completed}/${total}` : 'Calibrating… measuring endpoint')
    : 'Calibrate to my setup';
  // What the run changed, in plain terms — closes the user's "what happens after?" gap.
  const changed = result
    ? (Object.keys(t2?.entries || {}).length || result.tier1?.profile === 'lean'
        ? `set text budget ${t2?.winner_max_text_chars} chars · ${result.tier1?.profile} profile`
        : 'no change — your endpoint is already fast (kept the full defaults)')
    : '';

  return (
    <SectionCard
      title="Calibrate to my setup"
      description={
        'Tunes the deep-review TEXT BUDGET + lean/full profile to YOUR LLM endpoint — so '
        + 'reviews run as fast as your hardware allows with no loss of completeness. Runs ~6 '
        + 'real reviews (3 papers × 2 budgets), about a minute; progress shows below. Needs '
        + 'at least one built paper brief first (open a paper’s deep review). Saves to '
        + 'data/calibration.json automatically (applied on the next config load).'
      }
    >
      <div className="flex flex-wrap items-center gap-3">
        <Button type="button" onClick={run} disabled={running}>
          {progressLabel}
        </Button>
        {running && job.phase && <span className="text-xs text-slate-500">{job.phase}</span>}
        {result && (
          <span className="text-xs text-emerald-700">
            {result.provider}/{result.model} · {result.papers} paper(s) · {changed}
          </span>
        )}
      </div>
      {jobError && (
        <Banner kind={jobError.includes('no built') ? 'info' : 'error'}>
          {jobError.includes('no built')
            ? 'Open one paper’s deep review first, then calibrate (it measures on a built brief).'
            : `Calibration failed: ${jobError}`}
        </Banner>
      )}
      {error && <Banner kind="error">Calibration failed: {error}</Banner>}
    </SectionCard>
  );
}
