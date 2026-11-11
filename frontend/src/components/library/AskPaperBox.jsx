import { useEffect, useRef, useState } from 'react';
import { askPaper, saveReviewQuestion } from '../../api/libraryApi.js';
import { queueQuestionSubmission } from '../../offlineStore.js';
import { questionSubmissionError, sameQuestion } from '../../questionSubmission.js';
import Spinner from '../ui/Spinner.jsx';
import { Disclosure, SectionLabel } from '../paper/review/primitives.jsx';

const MODE_HELP =
  'Full context: metadata + generated notes + paper body. ' +
  'Fast retrieval: only the top matching passages. ' +
  'Full text: the raw extracted paper body only.';

// Standing, clinician-relevant starter questions for the rail chat. Research
// (AI2 Paper Plain) found a curated key-question index was readers' single
// most-preferred feature (18/20). Each runs the SAME grounded/abstaining QA and
// maps to a rubric concern the deep review already evaluates; they vanish after
// the first question.
const SUGGESTED_QUESTIONS = [
  ['code_released', 'Is the code released?'],
  ['how_evaluated', 'How is it evaluated?'],
  ['prompts_published', 'Are the prompts / protocol published?'],
  ['trust_less', 'Where should I trust it less?'],
];

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
// invented one. Answers stay session-local; question comments are durable.
export default function AskPaperBox({ itemKey, variant = 'disclosure', allowRawPdfModes = true, onSaved, onMirrorStart }) {
  const [question, setQuestion] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [mode, setMode] = useState('comprehensive');
  const [history, setHistory] = useState([]);
  const [commentStatus, setCommentStatus] = useState('');
  const [retryIntent, setRetryIntent] = useState(null);
  const mountedRef = useRef(true);
  const sessionRef = useRef(0);
  const busyRef = useRef(false);
  // The question whose Ask has not produced an answer yet: asking the same text again is a retry,
  // so it reuses its submission id instead of saving a second identical question row.
  const unansweredRef = useRef(null);

  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);
  useEffect(() => {
    sessionRef.current += 1;
    busyRef.current = false; unansweredRef.current = null;
    setQuestion(''); setBusy(false); setError(null);
    setHistory([]); setCommentStatus(''); setRetryIntent(null);
  }, [itemKey]);

  useEffect(() => {
    if (!allowRawPdfModes && mode !== 'comprehensive') setMode('comprehensive');
  }, [allowRawPdfModes, mode]);

  async function answerIntent(intent) {
    if (busyRef.current) return;
    const session = sessionRef.current;
    const current = () => mountedRef.current && session === sessionRef.current;
    const { submission, options } = intent;
    let stage = 'Question not saved on this device';
    let committed = false;
    busyRef.current = true;
    setBusy(true); setError(null); setCommentStatus('');
    const reportMirror = onMirrorStart?.({ question: submission });
    try {
      await queueQuestionSubmission(submission);
      committed = true;
      unansweredRef.current = submission;
      if (!current()) return;
      setRetryIntent(intent);
      setCommentStatus('Saved on this device — server comment pending.');
      onSaved?.();
      if (!navigator.onLine) { setQuestion(''); return; }
      stage = 'Server comment not confirmed';
      const saved = await saveReviewQuestion(submission);
      if (saved?.question_saved !== true || !saved.question_submission
          || !sameQuestion(saved.question_submission, submission)) {
        throw new Error('Invalid question save acknowledgement');
      }
      if (!current()) return;
      setCommentStatus('Question saved in the app — see My notes for Zotero delivery.');
      reportMirror?.(saved.question_mirror);
      onSaved?.();
      stage = 'Ask failed';
      const res = await askPaper(submission.item_key, submission.question, options);
      if (!current()) return;
      setHistory((prev) => [{ ...res, id: submission.submission_id, q: submission.question }, ...prev]);
      setQuestion(''); setRetryIntent(null); unansweredRef.current = null;
    } catch (err) {
      if (current()) {
        setError(`${stage}: ${err.message || String(err)}`);
        // Retrying cannot help a paper that has no PDF; the next Ask of the same text still reuses the id.
        if (err.body?.error === 'needs_pdf') setRetryIntent(null);
      }
    } finally {
      if (committed) {
        onSaved?.();
        window.dispatchEvent(new Event('zs-sync-request'));
      }
      if (current()) { busyRef.current = false; setBusy(false); }
    }
  }

  function ask(raw, presetId = null) {
    if (busyRef.current) return;
    const q = String(raw || '').trim();
    if (!q) return;
    const fields = { item_key: itemKey, question: q, origin: presetId ? 'preset' : 'typed', preset_id: presetId };
    const prev = unansweredRef.current;
    const retry = prev && sameQuestion(prev, { ...fields, submission_id: prev.submission_id });
    const submission = { submission_id: retry ? prev.submission_id : crypto.randomUUID(), ...fields };
    const invalid = questionSubmissionError(submission);
    if (invalid) { setError(invalid); return; }
    const prior = history.slice(0, 20).reverse().map((entry) => ({
      question: entry.q, answer: entry.answer, quote: entry.quote,
      evidence_handle: entry.evidence_handle,
    }));
    setQuestion(q); setRetryIntent(null);
    answerIntent({ submission, options: {
      submissionId: submission.submission_id, origin: submission.origin, presetId,
      mode, history: prior,
    } });
  }

  function handleAsk(e) {
    e.preventDefault();
    ask(question);
  }

  const rail = variant === 'rail';

  const inner = (
      <div className="space-y-3">
        {rail && history.length === 0 && (
          <div className="flex flex-wrap gap-1.5">
            {SUGGESTED_QUESTIONS.map(([presetId, q]) => (
              <button
                key={q}
                type="button"
                disabled={busy}
                onClick={() => ask(q, presetId)}
                className="rounded-full border border-slate-200 bg-white px-2 py-0.5 text-[11px] text-slate-500 hover:border-teal-300 hover:text-teal-700 disabled:opacity-50"
              >
                {q}
              </button>
            ))}
          </div>
        )}
        <form onSubmit={handleAsk} className={rail ? 'space-y-2' : 'flex flex-wrap items-center gap-2'}>
          <label className="sr-only" htmlFor="ask-q">Question about this paper</label>
          <input
            id="ask-q"
            type="text"
            aria-label="Question about this paper"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder="e.g. What dataset did they train on?"
            className={`${rail ? 'w-full' : 'flex-1 min-w-0'} px-2.5 py-1.5 rounded-lg border border-slate-300 text-[13px] focus:outline-none focus:ring-1 focus:ring-teal-500`}
            disabled={busy}
          />
          <div className={rail ? 'flex items-center gap-2' : 'contents'}>
            {allowRawPdfModes ? (
              <>
                <label className="sr-only" htmlFor="ask-mode">Answer mode</label>
                <select
                  id="ask-mode"
                  aria-label="Answer mode"
                  value={mode}
                  onChange={(e) => setMode(e.target.value)}
                  disabled={busy}
                  className={`${rail ? 'min-w-0 flex-1' : ''} px-2 py-1.5 rounded-lg border border-slate-300 text-[13px] bg-white`}
                  title={MODE_HELP}
                >
                  <option value="comprehensive">Full context</option>
                  <option value="retrieval">Fast retrieval</option>
                  <option value="full_text">Full text</option>
                </select>
              </>
            ) : (
              <span className={`${rail ? 'min-w-0 flex-1' : ''} px-2 py-1.5 rounded-lg border border-slate-200 bg-slate-50 text-[13px] text-slate-500`}>
                Full context
              </span>
            )}
            <button
              type="submit"
              disabled={busy || !question.trim()}
              className="inline-flex items-center gap-2 px-3.5 py-1.5 rounded-lg bg-teal-700 text-white text-[13px] font-semibold hover:bg-teal-800 disabled:opacity-50"
              title="Answered from this paper's generated artifact and text only"
            >
              {busy && <Spinner size="sm" color="teal-on-fill" />}
              {busy ? 'Reading…' : 'Ask'}
            </button>
          </div>
        </form>
        {busy && (
          <div role="status" aria-live="polite" className="sr-only">Reading the paper…</div>
        )}
        {commentStatus && <p role="status" className="text-[12px] text-slate-600 break-words">{commentStatus}</p>}
        {error && <div role="alert" className="text-[12px] text-rose-700 break-words">{error}</div>}
        {retryIntent && !busy && (
          <button type="button" onClick={() => answerIntent(retryIntent)} className="text-[12px] font-semibold text-teal-700">
            Retry
          </button>
        )}
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
                <div className="text-[11px] text-slate-400">
                  {metaLine(entry)}{entry.citation?.quote_verified ? ' · verified quote' : ''}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
  );

  // The story-page rail keeps the chat always open (a calm grounded inspector, not
  // a fold); every other surface keeps the disclosure (Jakob's Law — unchanged).
  if (variant === 'rail') {
    return (
      <div>
        <SectionLabel level={3} className="mb-2">Ask this paper</SectionLabel>
        <p className="mb-2 text-[11px] text-slate-400">Answered only from this paper — it abstains if the answer isn't there.</p>
        {inner}
      </div>
    );
  }
  return <Disclosure summary="Ask the paper">{inner}</Disclosure>;
}
