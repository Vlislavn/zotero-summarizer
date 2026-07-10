import { useCallback, useEffect, useRef, useState } from 'react';
import {
  screen as screenApi,
  startReview,
  getSession,
} from '../api/searchApi.js';
import { ErrorBanner, StatusBanner } from '../components/library/shared.jsx';
import { humanizeError } from '../utils/humanizeError.js';

// Targeted Search — the query-driven pull surface (services/search). Give a
// research topic, get a per-source query plan, a federated + deduped + relevance-
// ranked candidate list (SCREEN), then trigger the light+deep review (REVIEW,
// background-polled). Quality is measured on the top band BEFORE the deep set is
// chosen, so a rigorous paper ranked 6th can still be deep-read.

const BAND_CLASS = {
  highlight: 'bg-emerald-100 text-emerald-800',
  flag: 'bg-rose-100 text-rose-800',
  uncertain: 'bg-slate-100 text-slate-600',
};

function QueryPlan({ plan }) {
  const rows = [
    ['library', plan.library_expanded || plan.library_raw],
    ['openalex (lexical)', plan.openalex_lexical],
    ['openalex (semantic)', plan.openalex_semantic],
    ['europepmc', plan.europepmc],
    ['arxiv', plan.arxiv],
  ].filter(([, q]) => q);
  if (!rows.length) return null;
  return (
    <details className="mb-4 text-[12px]">
      <summary className="cursor-pointer text-slate-600 font-semibold">Query plan (per source)</summary>
      <div className="mt-2 grid gap-1">
        {rows.map(([src, q]) => (
          <div key={src} className="flex gap-2">
            <span className="text-slate-500 w-40 shrink-0">{src}</span>
            <span className="mono text-slate-800">{q}</span>
          </div>
        ))}
      </div>
    </details>
  );
}

function GroundedRow({ label, row }) {
  if (!row || (!row.text && !(row.supporting_quotes || []).length)) return null;
  return (
    <div className="mt-2">
      {label && <div className="text-[11px] font-semibold text-slate-500">{label}</div>}
      {row.text && <div className="text-[13px] text-slate-800">{row.text}</div>}
      {(row.supporting_quotes || []).map((q, i) => (
        <blockquote key={i} className="mt-1 border-l-2 border-slate-300 pl-2 text-[12px] italic text-slate-600">
          {q}
        </blockquote>
      ))}
    </div>
  );
}

function ReviewPanel({ review }) {
  if (!review || review.state === 'needs_full_text') {
    return <div className="mt-2 text-[12px] text-slate-500">No open-access full text — not deep-reviewed.</div>;
  }
  const brief = review.brief || {};
  return (
    <div className="mt-3 border-t border-slate-200 pt-2">
      {brief.tldr && <div className="text-[13px] text-slate-900"><span className="font-semibold">TL;DR:</span> {brief.tldr}</div>}
      <GroundedRow label="For your query" row={review.query_lens} />
      {(review.question_answers || []).map((qa, i) => (
        <GroundedRow key={i} label={qa.question} row={qa} />
      ))}
      {brief.key_findings?.length > 0 && (
        <ul className="mt-2 list-disc pl-5 text-[12px] text-slate-700">
          {brief.key_findings.map((f, i) => <li key={i}>{f}</li>)}
        </ul>
      )}
    </div>
  );
}

function CandidateCard({ cand }) {
  const sources = [...new Set((cand.provenance || []).map((p) => p.source))];
  const band = (cand.quality?.quality_band || '').toLowerCase();
  const meta = [cand.venue, cand.year].filter(Boolean).join(' · ');
  return (
    <div className={`border rounded-xl p-3 ${cand.is_retracted ? 'bg-rose-50 border-rose-300' : 'bg-white border-slate-200'}`}>
      <div className="flex items-baseline justify-between gap-3">
        <div className="flex-1 min-w-0">
          <div className="font-semibold text-slate-900">
            {cand.url ? <a href={cand.url} target="_blank" rel="noreferrer" className="hover:underline">{cand.title}</a> : cand.title}
          </div>
          {meta && <div className="text-[12px] text-slate-500">{meta}</div>}
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {typeof cand.query_score === 'number' && (
            <span className="mono text-[11px] text-slate-500" title="query relevance (our cross-encoder)">
              rel {cand.query_score.toFixed(2)}
            </span>
          )}
          {band && <span className={`text-[11px] px-2 py-0.5 rounded-full ${BAND_CLASS[band] || BAND_CLASS.uncertain}`}>{band}</span>}
        </div>
      </div>
      <div className="mt-1 flex flex-wrap gap-2 text-[11px] text-slate-500">
        {sources.map((s) => <span key={s} className="px-1.5 py-0.5 bg-slate-100 rounded">{s}</span>)}
        {cand.version_type && cand.version_type !== 'unknown' && (
          <span className="px-1.5 py-0.5 bg-indigo-50 text-indigo-700 rounded">{cand.version_type}</span>
        )}
        {cand.is_retracted && <span className="px-1.5 py-0.5 bg-rose-200 text-rose-900 rounded font-semibold">RETRACTED</span>}
      </div>
      {cand.abstract && <p className="mt-2 text-[12px] text-slate-600 line-clamp-3">{cand.abstract}</p>}
      <ReviewPanel review={cand.review} />
    </div>
  );
}

export default function Search() {
  const [query, setQuery] = useState('');
  const [questions, setQuestions] = useState('');
  const [session, setSession] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const pollRef = useRef(null);

  useEffect(() => () => clearInterval(pollRef.current), []);

  const runScreen = useCallback(async (e) => {
    e?.preventDefault();
    if (!query.trim()) return;
    setLoading(true);
    setError(null);
    setSession(null);
    try {
      const qs = questions.split('\n').map((q) => q.trim()).filter(Boolean);
      setSession(await screenApi({ query: query.trim(), questions: qs }));
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  }, [query, questions]);

  const runReview = useCallback(async () => {
    if (!session) return;
    setError(null);
    try {
      await startReview(session.id);
      setSession((s) => ({ ...s, status: 'reviewing' }));
      clearInterval(pollRef.current);
      pollRef.current = setInterval(async () => {
        const fresh = await getSession(session.id);
        setSession(fresh);
        if (fresh.status === 'reviewed' || fresh.status === 'error') clearInterval(pollRef.current);
      }, 3000);
    } catch (err) {
      setError(err);
    }
  }, [session]);

  const reviewing = session?.status === 'reviewing';
  return (
    <div className="max-w-3xl mx-auto">
      <h1 className="text-lg font-semibold text-slate-900 mb-1">Targeted Search</h1>
      <p className="text-[12px] text-slate-500 mb-3">
        Describe a research topic. We plan a query per source, federate the open literature,
        dedupe version families, rank by real relevance, then deep-read the best few.
      </p>
      <form onSubmit={runScreen} className="mb-4 grid gap-2">
        <textarea
          className="border border-slate-300 rounded-lg p-2 text-[14px]" rows={2}
          placeholder="e.g. LLM agents for clinical decision support in oncology"
          value={query} onChange={(e) => setQuery(e.target.value)}
        />
        <textarea
          className="border border-slate-200 rounded-lg p-2 text-[12px]" rows={2}
          placeholder="Optional — one specific question per line"
          value={questions} onChange={(e) => setQuestions(e.target.value)}
        />
        <button
          type="submit" disabled={loading || !query.trim()}
          className="justify-self-start px-4 py-1.5 rounded-lg bg-slate-900 text-white text-[13px] disabled:opacity-40"
        >
          {loading ? 'Searching…' : 'Search'}
        </button>
      </form>

      {error && <ErrorBanner error={humanizeError(error)} />}

      {session && (
        <div>
          {session.intent && session.intent.parse_ok === false && (
            <div className="mb-3 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-[12px] text-amber-800">
              Couldn’t structure this topic — searched your raw query as-is. Try rephrasing for a sharper plan.
            </div>
          )}
          <QueryPlan plan={session.plan || {}} />
          <div className="flex items-center justify-between mb-2">
            <div className="text-[12px] text-slate-500">{(session.candidates || []).length} candidates</div>
            <button
              onClick={runReview} disabled={reviewing || !(session.candidates || []).length}
              className="px-3 py-1 rounded-lg border border-slate-300 text-[12px] disabled:opacity-40"
            >
              {reviewing ? 'Reviewing…' : 'Deep-review top papers'}
            </button>
          </div>
          {session.status === 'error' && <StatusBanner isError message="Review failed — see server log." />}
          <div className="grid gap-3">
            {(session.candidates || []).map((c) => <CandidateCard key={c.candidate_id || c.title} cand={c} />)}
          </div>
        </div>
      )}
    </div>
  );
}
