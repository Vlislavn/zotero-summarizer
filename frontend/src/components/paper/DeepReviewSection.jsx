import { useEffect, useRef, useState } from 'react';
import { runDeepReview, fetchDeepReviewStatus } from '../../api/libraryApi.js';
import { fetchLlmReachability } from '../../api/settingsApi.js';
import { GRADE_CLS, formatShortDate } from '../library/shared.jsx';
import Spinner from '../ui/Spinner.jsx';

const DECISION_CLS = {
  read: 'bg-emerald-100 text-emerald-800 border-emerald-300',
  skim: 'bg-amber-100 text-amber-800 border-amber-300',
  skip: 'bg-slate-100 text-slate-600 border-slate-300',
};

// "92" -> "1m 32s", "8" -> "8s". Used for the live elapsed + ETA readout so the
// running review reports real progress instead of a fixed "~1–2 min" guess.
function formatDuration(seconds) {
  const s = Math.max(0, Math.round(Number(seconds) || 0));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rem = s % 60;
  return rem ? `${m}m ${rem}s` : `${m}m`;
}

function Section({ label, value }) {
  if (!value) return null;
  return (
    <p className="text-[11px] text-slate-700">
      <span className="font-semibold text-slate-500">{label}:</span> {value}
    </p>
  );
}

function BulletSection({ label, items }) {
  const list = (items || []).filter(Boolean);
  if (list.length === 0) return null;
  return (
    <div className="text-[11px] text-slate-700">
      <span className="font-semibold text-slate-500">{label}:</span>
      <ul className="list-disc ml-4">
        {list.map((x, i) => (
          <li key={i}>{x}</li>
        ))}
      </ul>
    </div>
  );
}

// Condensed paper digest: a 1-line headline (read/skip + quality grade + TL;DR),
// with the full referee investigation behind one "Details" expander (Cognitive
// Load / Miller's Law). Replaces the old contradictory relevance re-score.
function DigestBlock({ deep }) {
  const d = deep.digest;
  if (!d) {
    // Legacy cache entry (pre-digest) — show the old grade if present, else nudge.
    const q = deep.quality;
    if (q && q.grade) {
      return (
        <div className="rounded-xl border border-slate-200 bg-slate-50 p-3 text-xs text-slate-600">
          <span className="font-semibold">Quality {q.grade}</span>{q.verdict ? ` — ${q.verdict}` : ''}
          <div className="text-[10px] text-slate-400 mt-1">Older review — re-run for the new digest.</div>
        </div>
      );
    }
    return null;
  }
  const decision = d.read_decision || '';
  const decisionCls = DECISION_CLS[decision] || 'bg-slate-100 text-slate-600 border-slate-300';
  return (
    <div className="rounded-xl border border-indigo-200 bg-indigo-50/40 p-3 space-y-2">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-[10px] uppercase tracking-wider font-semibold text-indigo-500">Digest</span>
        {decision && (
          <span className={`px-2 py-0.5 rounded-full text-[11px] font-bold border ${decisionCls}`} title="Read / skim / skip recommendation">
            {decision}
          </span>
        )}
        {d.grade && (
          <span className={`px-2 py-0.5 rounded-full text-[11px] font-bold border ${GRADE_CLS[d.grade] || 'bg-slate-100 text-slate-700 border-slate-300'}`} title="Full-text quality grade">
            Quality {d.grade}
          </span>
        )}
      </div>
      {d.tldr && <p className="text-xs text-slate-800">{d.tldr}</p>}
      <details>
        <summary className="cursor-pointer text-[11px] uppercase tracking-wider font-semibold text-slate-500 select-none">
          Details
        </summary>
        <div className="mt-1.5 space-y-1">
          <Section label="Verdict" value={d.verdict} />
          <Section label="Why" value={d.read_why} />
          <BulletSection label="Read parts" items={d.read_parts} />
          <Section label="Relevance" value={d.relevance} />
          <Section label="Controversies" value={d.controversies} />
          <Section label="Impact" value={d.impact} />
          <Section label="Unknown unknowns" value={d.unknown_unknowns} />
          <BulletSection label="Implementation" items={d.implementation} />
          <div className="grid grid-cols-2 gap-x-4 gap-y-0.5 text-[11px] text-slate-600 pt-1">
            <span>Soundness: <b>{d.soundness}/5</b></span>
            <span>Novelty: <b>{d.novelty}/5</b></span>
            <span>Significance: <b>{d.significance}/5</b></span>
            <span>Reproducibility: <b>{d.reproducibility}/5</b></span>
            <span>Clarity: <b>{d.clarity}/5</b></span>
            <span>Confidence: <b>{Math.round((d.confidence || 0) * 100)}%</b></span>
          </div>
          {d.key_strength && <p className="text-[11px] text-emerald-700">+ {d.key_strength}</p>}
          {d.key_weakness && <p className="text-[11px] text-rose-700">− {d.key_weakness}</p>}
        </div>
      </details>
      {deep.reviewed_at && <div className="text-[10px] text-slate-400">reviewed {formatShortDate(deep.reviewed_at)}</div>}
      {deep.zotero_note_written && <div className="text-[10px] text-emerald-600">saved to Zotero ✓</div>}
      {deep.zotero_note_error && (
        <div className="text-[10px] text-amber-600">note not written: {deep.zotero_note_error}</div>
      )}
    </div>
  );
}

// Per-paper deep review: shows the cached digest (if any) + a button that runs a
// full-text LLM digest and polls until done, then calls onDone() to refetch the
// detail. Pre-empts no-PDF and an already-running review. Shared by Library +
// Annotate.
export default function DeepReviewSection({ itemKey, deep, onDone, hasPdf = true }) {
  const [status, setStatus] = useState({ status: 'idle', completed: 0, total: 0, error: null });
  const [error, setError] = useState(null);
  const [focusPrompt, setFocusPrompt] = useState('');
  // Reachability of the deep_review LLM endpoint (null = unknown/probing).
  const [llm, setLlm] = useState(null);
  const pollRef = useRef(null);

  // Proactively probe the deep_review endpoint so an unreachable model is
  // announced BEFORE the user clicks Run — the failure that silently produced an
  // empty brief. Cheap GET /models; a probe error is advisory and just hides the
  // banner (never blocks the section).
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const h = await fetchLlmReachability();
        if (cancelled) return;
        setLlm((h.stages || []).find((s) => s.stage === 'deep_review') || null);
      } catch {
        /* advisory probe — ignore, the run path still surfaces real errors */
      }
    })();
    return () => { cancelled = true; };
  }, []);

  // On mount, reflect any already-running review so the button doesn't silently
  // no-op (deep review is global single-flight — one at a time).
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const s = await fetchDeepReviewStatus();
      if (cancelled) return;
      if (s.status === 'running') { setStatus(s); poll(); }
    })();
    return () => {
      cancelled = true;
      if (pollRef.current) { clearTimeout(pollRef.current); pollRef.current = null; }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function poll() {
    if (pollRef.current) { clearTimeout(pollRef.current); pollRef.current = null; }
    pollRef.current = setTimeout(async () => {
      const s = await fetchDeepReviewStatus();
      setStatus(s);
      if (s.status === 'running') poll();
      else onDone?.();
    }, 3000);
  }

  async function handleRun() {
    setError(null);
    try {
      const s = await runDeepReview({ itemKey, focusPrompt });
      setStatus(s);
      if (s.status === 'running') poll();
      else onDone?.();
    } catch (e) {
      setError(`Deep review failed: ${e.message || e}`);
    }
  }

  const running = status.status === 'running';
  return (
    <div className="space-y-2">
      {llm && llm.reachable === false && (
        <div className="rounded-xl border border-rose-300 bg-rose-50 p-3 text-xs text-rose-800" role="alert">
          <span className="font-semibold">Deep-review model unreachable.</span>{' '}
          <span className="font-mono">{llm.model || '(model unset)'}</span> at{' '}
          <span className="font-mono">{llm.base_url || '(no base URL)'}</span> isn’t responding,
          so a review will produce no digest. Start that server, or pick a reachable model in{' '}
          <span className="font-semibold">Settings → LLM routing</span>.
          {llm.detail && <div className="mt-1 text-[11px] text-rose-600 break-words">{llm.detail}</div>}
        </div>
      )}
      {deep && deep.needs_pdf && (
        <div className="rounded-xl border border-amber-200 bg-amber-50/60 p-3 text-xs text-amber-800">
          <span className="font-semibold">No PDF yet.</span>{' '}
          In Zotero, select this paper and run <span className="font-semibold">“Find Available PDF”</span>, then re-run.
        </div>
      )}
      {deep && !deep.needs_pdf && <DigestBlock deep={deep} />}

      {!hasPdf ? (
        <div className="text-[11px] text-amber-700">
          Needs a PDF — fetch it in Zotero first, then deep-review.
        </div>
      ) : (
        <div className="space-y-1.5">
          <textarea
            value={focusPrompt}
            onChange={(e) => setFocusPrompt(e.target.value)}
            disabled={running}
            placeholder="Optional focus (e.g. 'highlight clinical applicability', 'I care about reproducibility')"
            maxLength={1000}
            rows={2}
            className="w-full rounded-lg border border-slate-200 px-2.5 py-1.5 text-[11px] text-slate-700 placeholder-slate-400 resize-none focus:outline-none focus:border-indigo-400 disabled:opacity-50"
          />
          <button
            type="button"
            onClick={handleRun}
            disabled={running}
            className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg bg-indigo-600 text-white text-xs font-semibold hover:bg-indigo-700 disabled:opacity-50"
            title="Run a condensed full-text digest (what it's about + how to use it + quality)"
          >
            {running && <Spinner size="sm" color="indigo-on-fill" />}
            {running
              ? `Analyzing the full text…${
                  status.progress?.eta_seconds != null
                    ? ` (~${formatDuration(status.progress.eta_seconds)} left)`
                    : ''
                }`
              : 'Run deeper review'}
          </button>
        </div>
      )}
      {running && (
        <div className="text-[11px] text-indigo-600 space-y-0.5">
          <div>A deep review is running — the digest appears here when it's done.</div>
          {status.progress?.phase_label && (
            <div className="font-semibold">
              {status.progress.phase_label}
              {status.progress.sub?.total > 0 &&
                ` ${status.progress.sub.done}/${status.progress.sub.total}`}
              {status.total > 1 && ` · paper ${status.completed + 1} of ${status.total}`}
            </div>
          )}
          {status.progress?.total_elapsed_seconds != null && (
            <div className="text-indigo-500">
              {formatDuration(status.progress.total_elapsed_seconds)} elapsed
              {status.progress.eta_seconds != null &&
                ` · ~${formatDuration(status.progress.eta_seconds)} remaining`}
            </div>
          )}
        </div>
      )}
      {status.status === 'error' && status.error && (
        <div className="text-[11px] text-rose-700">Deep review failed: {status.error}</div>
      )}
      {error && <div className="text-[11px] text-rose-700">{error}</div>}
    </div>
  );
}
