import { useCallback, useEffect, useRef, useState } from 'react';
import {
  screen as screenApi,
  getSession,
  materialize as materializeApi,
} from '../api/searchApi.js';
import CollectionPicker from '../components/CollectionPicker.jsx';
import { ErrorBanner, StatusBanner } from '../components/library/shared.jsx';
import { humanizeError } from '../utils/humanizeError.js';

// Targeted Search — the query-driven pull surface (services/search). Give a
// research topic, get a per-source query plan, a federated + deduped + relevance-
// ranked candidate list (SCREEN), then trigger the light+deep review (REVIEW,
// background-polled). Quality is measured on the top band BEFORE the deep set is
// chosen, so a rigorous paper ranked 6th can still be deep-read.

// Full-text quality band chip. Von Restorff: relevance is the master signal (it owns
// the saturated pill), so a quality *win* stays neutral — only a quality *problem*
// (flag) earns a warning color. Keeps the card from emphasizing everything at once.
const BAND_CLASS = {
  highlight: 'bg-slate-100 text-slate-600',
  flag: 'bg-rose-100 text-rose-800',
  uncertain: 'bg-slate-100 text-slate-600',
};

// Relevance band (pool-relative, from query_score) — server field cand.relevance_band.
// Distinct from the full-text quality band above; drives the card accent + a pill.
const REL_BAND = {
  strong: { label: 'strong match', pill: 'bg-emerald-600 text-white', card: 'border-emerald-300 bg-emerald-50/40' },
  on_topic: { label: 'on-topic', pill: 'bg-sky-600 text-white', card: 'border-sky-200 bg-white' },
  weak: { label: 'weak match', pill: 'bg-slate-200 text-slate-600', card: 'border-slate-200 bg-slate-50/60' },
};

function QueryPlan({ plan }) {
  const rows = [
    ['library', plan.library_expanded || plan.library_raw],
    ['openalex (lexical)', plan.openalex_lexical],
    ['openalex (semantic)', plan.openalex_semantic],
    ['europepmc', plan.europepmc],
    ['arxiv', plan.arxiv],
    ['crossref', plan.crossref],
    ['semantic scholar', plan.semantic_scholar],
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

// Agentic-search rounds (Phase E) — one line per PRF round: what the LLM added to
// broaden the pool, what it dropped as off-topic, and how many new papers it pulled.
function Refinements({ rounds }) {
  if (!rounds || !rounds.length) return null;
  return (
    <div className="mb-4 rounded-lg border border-violet-200 bg-violet-50/60 px-3 py-2 text-[12px]">
      <div className="font-semibold text-violet-800 mb-1">Agentic refinement</div>
      {rounds.map((r) => (
        <div key={r.round} className="text-slate-700">
          <span className="text-slate-500">Round {r.round}:</span>{' '}
          {(r.add_concepts || []).length > 0 && <>added <span className="font-medium">{(r.add_concepts || []).join(', ')}</span></>}
          {(r.drop_terms || []).length > 0 && <> · dropped <span className="font-medium">{(r.drop_terms || []).join(', ')}</span></>}
          {typeof r.new_candidates === 'number' && <span className="text-slate-500"> (+{r.new_candidates} new)</span>}
        </div>
      ))}
    </div>
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

function CandidateCard({ cand, onAdd }) {
  const sources = [...new Set((cand.provenance || []).map((p) => p.source))];
  const band = (cand.quality?.quality_band || '').toLowerCase();
  const meta = [cand.venue, cand.year].filter(Boolean).join(' · ');
  const added = Boolean(cand.materialized_zotero_key || cand.existing_zotero_key);
  const rel = REL_BAND[cand.relevance_band];
  const why = Array.isArray(cand.why) ? cand.why : [];
  const [adding, setAdding] = useState(false);
  const [addErr, setAddErr] = useState(null);
  const add = useCallback(async () => {
    setAdding(true);
    setAddErr(null);
    try {
      await onAdd(cand.candidate_id);
    } catch (err) {
      setAddErr(err);
    } finally {
      setAdding(false);
    }
  }, [onAdd, cand.candidate_id]);
  const cardCls = cand.is_retracted ? 'bg-rose-50 border-rose-300' : (rel?.card || 'bg-white border-slate-200');
  return (
    <div className={`border rounded-xl p-3 ${cardCls}`}>
      <div className="flex items-baseline justify-between gap-3">
        <div className="flex-1 min-w-0">
          <div className="font-semibold text-slate-900">
            {cand.url ? <a href={cand.url} target="_blank" rel="noreferrer" className="hover:underline">{cand.title}</a> : cand.title}
          </div>
          {meta && <div className="text-[12px] text-slate-500">{meta}</div>}
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {rel && <span className={`text-[11px] px-2 py-0.5 rounded-full font-semibold ${rel.pill}`}>{rel.label}</span>}
          {typeof cand.query_score === 'number' && (
            <span className="mono text-[11px] text-slate-500" title="query relevance (our cross-encoder), 0–1">
              {cand.query_score.toFixed(2)}
            </span>
          )}
          {band && <span className={`text-[11px] px-2 py-0.5 rounded-full ${BAND_CLASS[band] || BAND_CLASS.uncertain}`} title="full-text quality band">{band}</span>}
        </div>
      </div>
      {/* why chips: secondary context → neutral, so they don't compete with the band
          pill or the Add CTA for attention (Von Restorff — emphasize sparingly). */}
      {why.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          {why.map((r) => (
            <span key={r} className="inline-flex items-center px-2 py-0.5 rounded-full border border-slate-200 bg-slate-50 text-slate-600 text-[11px] font-medium">{r}</span>
          ))}
        </div>
      )}
      <div className="mt-1 flex flex-wrap gap-2 text-[11px] text-slate-500">
        {sources.map((s) => <span key={s} className="px-1.5 py-0.5 bg-slate-100 rounded">{s}</span>)}
        {cand.version_type && cand.version_type !== 'unknown' && (
          <span className="px-1.5 py-0.5 bg-slate-100 rounded">{cand.version_type}</span>
        )}
        {cand.is_retracted && <span className="px-1.5 py-0.5 bg-rose-200 text-rose-900 rounded font-semibold">RETRACTED</span>}
      </div>
      {cand.abstract && <p className="mt-2 text-[12px] text-slate-600 line-clamp-3">{cand.abstract}</p>}
      <ReviewPanel review={cand.review} />
      <div className="mt-2 flex items-center gap-2">
        {added ? (
          <span className="text-[12px] text-emerald-700 font-semibold">✓ In library</span>
        ) : (
          <button
            type="button" onClick={add} disabled={adding}
            className="px-2.5 py-1 rounded-lg text-[12px] font-semibold bg-emerald-600 text-white hover:bg-emerald-700 disabled:opacity-40"
          >
            {adding ? 'Adding…' : 'Add to library'}
          </button>
        )}
        {addErr && <span className="text-[11px] text-rose-700">{humanizeError(addErr)}</span>}
      </div>
    </div>
  );
}

// The search session lives on the SERVER; the page only needs to survive a tab
// switch with the POINTER (session id) + the typed drafts (Working Memory: the
// system remembers, not the user). sessionStorage, not URL — a session id is not
// a shareable address, and it dies with the browser tab, matching its lifetime.
const SS_KEY = 'zs.searchSession';
function loadSaved() {
  try { return JSON.parse(sessionStorage.getItem(SS_KEY)) || {}; } catch { return {}; }
}

export default function Search() {
  const [saved] = useState(loadSaved);   // snapshot once, at mount
  const [query, setQuery] = useState(saved.q || '');
  const [questions, setQuestions] = useState(saved.qs || '');
  const [session, setSession] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  // Target Zotero collection for per-result "Add to library" ('' = server "Inbox").
  const [targetCollection, setTargetCollection] = useState(saved.tc || '');
  const pollRef = useRef(null);
  // The id to persist while no live session exists yet (cleared if it turns out dead).
  const savedIdRef = useRef(saved.id || null);

  useEffect(() => () => clearInterval(pollRef.current), []);

  // Mirror pointer + drafts to sessionStorage on every change.
  useEffect(() => {
    sessionStorage.setItem(SS_KEY, JSON.stringify(
      { id: session?.id || savedIdRef.current, q: query, qs: questions, tc: targetCollection },
    ));
  }, [session?.id, query, questions, targetCollection]);

  // File one candidate into the chosen collection, then stamp its returned key on
  // the local session so the card flips to "✓ In library" without waiting for a poll.
  const materializeCard = useCallback(async (candidateId) => {
    if (!session) return;
    const res = await materializeApi(session.id, candidateId, targetCollection);
    setSession((prev) => (prev ? {
      ...prev,
      candidates: (prev.candidates || []).map((c) =>
        c.candidate_id === candidateId ? { ...c, materialized_zotero_key: res.zotero_key } : c),
    } : prev));
  }, [session, targetCollection]);

  // Poll a reviewing session until the deep reviews finish (screen auto-starts them).
  const pollUntilDone = useCallback((id) => {
    clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      const fresh = await getSession(id);
      setSession(fresh);
      if (fresh.status === 'reviewed' || fresh.status === 'error') clearInterval(pollRef.current);
    }, 3000);
  }, []);

  // Rehydrate the last session on remount (tab switch / reload): refetch from the
  // server (source of truth) and resume polling if the review is still running.
  // A dead pointer (server restarted, session GC'd) clears itself silently.
  useEffect(() => {
    if (!saved.id) return undefined;
    let cancelled = false;
    getSession(saved.id)
      .then((sess) => {
        if (cancelled) return;
        setSession(sess);
        if (sess.status === 'reviewing') pollUntilDone(sess.id);
      })
      .catch(() => {
        if (cancelled) return;
        savedIdRef.current = null;          // dead pointer — stop persisting it
        sessionStorage.removeItem(SS_KEY);
      });
    return () => { cancelled = true; };
  }, [saved.id, pollUntilDone]);

  const runScreen = useCallback(async (e) => {
    e?.preventDefault();
    if (!query.trim()) return;
    setLoading(true);
    setError(null);
    setSession(null);
    try {
      const qs = questions.split('\n').map((q) => q.trim()).filter(Boolean);
      const sess = await screenApi({ query: query.trim(), questions: qs });
      savedIdRef.current = sess.id;
      setSession(sess);
      if (sess.status === 'reviewing') pollUntilDone(sess.id);  // auto deep-review started server-side
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  }, [query, questions, pollUntilDone]);

  const reviewing = session?.status === 'reviewing';
  // Doherty: name the honest stage. During agentic rounds refinements grow but no
  // review has landed yet — showing "Deep-reviewing…" then would mislabel the wait.
  const anyReviewed = (session?.candidates || []).some((c) => c.review && c.review.state);
  const refining = reviewing && (session?.refinements || []).length > 0 && !anyReviewed;
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
          <Refinements rounds={session.refinements} />
          <div className="flex items-center justify-between gap-2 mb-2 flex-wrap">
            <div className="text-[12px] text-slate-500">{(session.candidates || []).length} candidates</div>
            <div className="flex items-center gap-2">
              {reviewing && (
                <div className="text-[12px] text-slate-500 flex items-center gap-1.5">
                  <span className="inline-block w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
                  {refining ? 'Refining the search…' : 'Deep-reviewing the top papers…'}
                </div>
              )}
              <span className="text-[11px] text-slate-500">Add to:</span>
              <CollectionPicker value={targetCollection} onChange={setTargetCollection} />
            </div>
          </div>
          {session.status === 'error' && <StatusBanner isError message="Review failed — see server log." />}
          {(() => {
            const cands = session.candidates || [];
            const strong = cands.filter((c) => c.relevance_band !== 'weak');
            const weak = cands.filter((c) => c.relevance_band === 'weak');
            return (
              <>
                <div className="grid gap-3">
                  {strong.map((c) => <CandidateCard key={c.candidate_id || c.title} cand={c} onAdd={materializeCard} />)}
                </div>
                {weak.length > 0 && (
                  <details className="mt-3">
                    <summary className="cursor-pointer text-[12px] text-slate-500 font-semibold py-1">
                      {weak.length} weaker match{weak.length === 1 ? '' : 'es'} ▾
                    </summary>
                    <div className="grid gap-3 mt-2">
                      {weak.map((c) => <CandidateCard key={c.candidate_id || c.title} cand={c} onAdd={materializeCard} />)}
                    </div>
                  </details>
                )}
              </>
            );
          })()}
        </div>
      )}
    </div>
  );
}
