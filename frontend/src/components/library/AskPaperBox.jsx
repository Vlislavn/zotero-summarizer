import { useEffect, useRef, useState } from 'react';
import { askPaper } from '../../api/libraryApi.js';
import Spinner from '../ui/Spinner.jsx';
import { Disclosure } from '../paper/review/primitives.jsx';

const MODE_HELP =
  'Comprehensive: metadata + generated notes + paper body. ' +
  'Fast retrieval: only the top matching passages. ' +
  'Full text: the raw extracted paper body only.';

// One metadata line per answer: latency (hidden for deterministic answers),
// the mode that actually ran, passages used, then the real model id.
function metaLine(entry) {
  const parts = [];
  if (entry.latency_seconds) parts.push(`${entry.latency_seconds}s`);
  if (entry.mode) parts.push(entry.mode);
  if (entry.chunks_used) parts.push(`${entry.chunks_used} passages`);
  if (entry.model) parts.push(entry.model);
  return parts.join(' · ');
}

// "Ask the paper": grounded Q&A against the LOCAL model with enforced
// abstention — an abstained answer renders as "not in the paper", never an
// invented one. Session-local history (newest first); nothing persists.
export default function AskPaperBox({ itemKey }) {
  const [question, setQuestion] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [mode, setMode] = useState('comprehensive');
  const [history, setHistory] = useState([]);
  const mountedRef = useRef(true);
  const idRef = useRef(0);

  useEffect(() => () => { mountedRef.current = false; }, []);

  async function handleAsk(e) {
    e.preventDefault();
    const q = question.trim();
    if (!q || busy) return;
    const askedKey = itemKey;
    setBusy(true);
    setError(null);
    try {
      const res = await askPaper(itemKey, q, { mode });
      // Drop a late answer if the component unmounted or the row changed.
      if (!mountedRef.current || askedKey !== itemKey) return;
      idRef.current += 1;
      setHistory((prev) => [{ id: idRef.current, q, ...res }, ...prev]);
      setQuestion('');
    } catch (err) {
      if (!mountedRef.current || askedKey !== itemKey) return;
      setError(err.message || String(err));
    } finally {
      if (mountedRef.current && askedKey === itemKey) setBusy(false);
    }
  }

  return (
    <Disclosure summary="Ask the paper">
      <div className="space-y-3">
        <form onSubmit={handleAsk} className="flex flex-wrap items-center gap-2">
          <label className="sr-only" htmlFor="ask-q">Question about this paper</label>
          <input
            id="ask-q"
            type="text"
            aria-label="Question about this paper"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder="e.g. What dataset did they train on?"
            className="flex-1 min-w-0 px-2.5 py-1.5 rounded-lg border border-slate-300 text-[13px] focus:outline-none focus:ring-1 focus:ring-teal-500"
            disabled={busy}
          />
          <label className="sr-only" htmlFor="ask-mode">Answer mode</label>
          <select
            id="ask-mode"
            aria-label="Answer mode"
            value={mode}
            onChange={(e) => setMode(e.target.value)}
            disabled={busy}
            className="px-2 py-1.5 rounded-lg border border-slate-300 text-[13px] bg-white"
            title={MODE_HELP}
          >
            <option value="comprehensive">Comprehensive</option>
            <option value="retrieval">Fast retrieval</option>
            <option value="full_text">Full text</option>
          </select>
          <button
            type="submit"
            disabled={busy || !question.trim()}
            className="inline-flex items-center gap-2 px-3.5 py-1.5 rounded-lg bg-teal-700 text-white text-[13px] font-semibold hover:bg-teal-800 disabled:opacity-50"
            title="Answered from this paper's generated artifact and text only"
          >
            {busy && <Spinner size="sm" color="teal-on-fill" />}
            {busy ? 'Reading…' : 'Ask'}
          </button>
        </form>
        {busy && (
          <div role="status" aria-live="polite" className="sr-only">Reading the paper…</div>
        )}
        {error && <div className="text-[12px] text-rose-700">Ask failed: {error}</div>}
        {/* Answers as hairline-separated reading rows — no slate card per answer. */}
        {history.length > 0 && (
          <div className="divide-y divide-slate-200/60">
            {history.map((entry) => (
              <div key={entry.id} className="py-2.5 first:pt-0 space-y-1">
                <p className="text-[12px] font-semibold text-slate-500">Q: {entry.q}</p>
                {entry.abstained ? (
                  <p className="text-[13px] leading-relaxed text-amber-700">
                    The paper doesn't contain this answer (the model abstained rather than guessing).
                  </p>
                ) : (
                  <p className="text-[13px] leading-relaxed text-slate-800">{entry.answer}</p>
                )}
                {entry.quote && (
                  <blockquote className="border-l-2 border-teal-300 pl-2 text-[12px] italic text-slate-500 leading-relaxed">
                    “{entry.quote}”
                  </blockquote>
                )}
                <div className="text-[11px] text-slate-400">{metaLine(entry)}</div>
              </div>
            ))}
          </div>
        )}
      </div>
    </Disclosure>
  );
}
