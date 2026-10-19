import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { Link, useInRouterContext, useLocation, useNavigate } from 'react-router-dom';
import {
  screen as screenApi,
  getSession,
  startReview,
  materialize as materializeApi,
} from '../api/searchApi.js';
import CollectionPicker from '../components/CollectionPicker.jsx';
import { ErrorBanner, StatusBanner } from '../components/library/shared.jsx';
import { humanizeError } from '../utils/humanizeError.js';
import { sortSearchCandidates } from '../utils/searchSort.js';
import { readStoredJson, writeStorage } from '../utils/safeStorage.js';
import { Comments } from '../components/paper/RelatedPapers.jsx';
import { pollSessionUntilDone } from '../searchPolling.js';

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
  const rows = plan.display || [];
  if (!rows.length) return null;
  return (
    <details className="mb-4 text-[12px]">
      <summary className="cursor-pointer text-slate-600 font-semibold">Query plan (per source)</summary>
      <div className="mt-2 grid gap-1">
        {rows.map(({ source: src, query: q }, index) => (
          <div key={index} className="flex gap-2">
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
          {(r.drop_terms || []).length > 0 && <> · de-emphasized (not excluded) <span className="font-medium">{(r.drop_terms || []).join(', ')}</span></>}
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
  if (!review || !Object.keys(review).length) return <p className="mt-2 text-xs">Not reviewed.</p>;
  if (review.state === 'error') return <p role="alert">Review failed: {review.error}</p>;
  if (review.state === 'needs_full_text') {
    return <div className="mt-2 text-[12px] text-slate-500">Full text unavailable after acquisition — not deep-reviewed.</div>;
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

const RETRIEVAL = {
  observed: 'Scheduled providers returned observed results.', partial: 'Partial provider coverage; this is not a complete search.',
  failed: 'Provider retrieval failed.', unknown: 'Provider availability is unknown.', not_attempted: 'Provider retrieval was not attempted.',
};
const ASSESSMENT = {
  not_requested: 'Private comment assessment was not requested.', assessed: 'Metadata-bound model judgment; not a full-text review or quality finding.',
  no_comments: 'Observed empty seed comments.', no_candidates: 'No candidates available for assessment.', disabled: 'Comment assessment is disabled.',
  budget_exceeded: 'Complete context exceeds the budget; it was not clipped.', context_unavailable: 'Seed context is unavailable or incomplete.',
  failed: 'Comment assessment failed.', interrupted: 'Assessment was interrupted; no successful completion is established.', stale: 'Assessment context changed.',
};

function DiscoveryEvidence({ row }) {
  if (!row) return null;
  return <div className="mt-2 text-sm">
    <p>{row.sentence}</p><p>Metadata-bound model judgment · relevance: {row.relevance}; preference: {row.preference}.</p>
    <details><summary>Metadata evidence</summary>
      <p>Quote membership is checked; entailment is not independently verified. Comments are context, not paper evidence.</p>
      {Object.entries(row.evidence || {}).map(([side, quote]) => <blockquote key={side} className="border-l pl-3">
        {side} · {quote.item_key} · {quote.field} [{quote.start}, {quote.end}]: “{quote.excerpt}”
      </blockquote>)}
      <p>Model-cited comment IDs: {(row.used_context_ids || []).join(', ') || 'none'}.</p>
    </details>
  </div>;
}

function DiscoveryStatus({ session, trusted }) {
  const discovery = session.discovery;
  const cited = trusted ? discovery.assessment.items.flatMap(row => row.used_context_ids || []) : [];
  return <section aria-label="Discovery status" className="mb-4 text-sm break-words">
    <h2 className="font-display text-xl">Find new relevant · {discovery.seed_title}</h2>
    <p role="status">Session: {session.status} · Freshness: {discovery.freshness}</p>
    <p aria-label="Retrieval status">OpenAlex semantic + lexical only · Retrieval: {discovery.retrieval.status}. {RETRIEVAL[discovery.retrieval.status]}</p>
    <p aria-label="Assessment status">Assessment: {discovery.assessment.status}. {ASSESSMENT[discovery.assessment.status]}</p>
    {discovery.retrieval.empty_reason && <p>{discovery.retrieval.empty_reason === 'providers_empty'
      ? 'Scheduled providers successfully returned no candidates.' : 'Observed candidates were all known saved.'}</p>}
    {trusted && !discovery.retrieval.empty_reason && !discovery.recommended_ids.length && <p>No supported default recommendations; retained observations may still be available below.</p>}
    {!trusted && <p>Private evidence hidden; freshness is stale, unverified, or awaiting a successful status read. New actions are unavailable.</p>}
    {discovery.error && <p role="alert">Discovery error: {discovery.error.stage} · {discovery.error.code}</p>}
    <Link className="text-forest underline" to={`/paper/${encodeURIComponent(discovery.seed_key)}?retryDiscovery=1`}>Return to seed to explicitly retry discovery</Link>
    <details><summary>Provider observations</summary>
      {discovery.retrieval.variants.map((variant, index) => <p key={index}>{variant.channel} · {variant.outcome?.status || 'unknown'} · count: {variant.outcome?.count ?? 'unknown'} · {variant.outcome?.error_code}</p>)}
    </details>
    {trusted && <>
      <p>{discovery.comments_in_ranking ? 'Model reported comment use; influence is unverified.' : 'No model-cited comment use reported.'}</p>
      <p>Submitted comment IDs (not proof of use): {discovery.assessment.submitted_context_ids.join(', ') || 'none'}.</p>
      <Comments comments={discovery.seed_comments} used={cited} submitted={discovery.assessment.submitted_context_ids} />
    </>}
  </section>;
}

function CandidateCard({ cand, onAdd, discovery, actionsAllowed = true, trusted = false }) {
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
    <article aria-label={cand.title} className={`border rounded-xl p-3 ${cardCls}`}>
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
      {discovery && <>
        {(discovery.unresolved_membership || []).includes(cand.candidate_id) || cand.in_library == null
          ? <p>Membership unknown — not established new.</p> : <p>{cand.in_library ? 'Known saved paper.' : 'Not saved in the checked membership snapshot.'}</p>}
        <DiscoveryEvidence row={trusted ? discovery.assessment.items.find(row => row.item_key === cand.candidate_id) : null} />
      </>}
      <ReviewPanel review={cand.review} />
      <div className="mt-2 flex items-center gap-2">
        {added ? (
          <span className="text-[12px] text-emerald-700 font-semibold">✓ In library</span>
        ) : (
          <button
            type="button" onClick={add} disabled={adding || !actionsAllowed}
            className="px-2.5 py-1 rounded-lg text-[12px] font-semibold bg-emerald-600 text-white hover:bg-emerald-700 disabled:opacity-40"
          >
            {adding ? 'Adding…' : discovery && cand.planned_zotero_key ? 'Reconcile retained Add' : 'Add to library'}
          </button>
        )}
        {addErr && <span className="text-[11px] text-rose-700">{humanizeError(addErr)}</span>}
      </div>
    </article>
  );
}

// The server owns sessions. URL pointers win; tab storage is best-effort restoration.
const SS_KEY = 'zs.searchSession';
const hasDiscovery = session => Boolean(session?.discovery && Object.keys(session.discovery).length);
function loadSaved() {
  return readStoredJson(
    SS_KEY,
    {},
    (value) => value && typeof value === 'object' && !Array.isArray(value),
    'sessionStorage',
  );
}

export default function Search() {
  return useInRouterContext() ? <SearchRoute /> : <SearchSession />;
}

function SearchRoute() {
  const location = useLocation();
  const navigate = useNavigate();
  return <SearchSession urlSearch={location.search} onPointer={id => navigate(`/search?session=${encodeURIComponent(id)}`, { replace: true })} />;
}

function SearchSession({ urlSearch = '', onPointer }) {
  const [saved] = useState(loadSaved);   // snapshot once, at mount
  const [query, setQuery] = useState(saved.q || '');
  const [questions, setQuestions] = useState(saved.qs || '');
  const [session, setSession] = useState(null);
  const [constraints, setConstraints] = useState({ must_include: "", must_not_include: "", study_types: "" });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  // Target Zotero collection for per-result "Add to library" ('' = server "Inbox").
  const [targetCollection, setTargetCollection] = useState(saved.tc || '');
  const [sort, setSort] = useState(saved.sort || 'recommended');
  const pollRef = useRef(null);
  const savedIdRef = useRef(new URLSearchParams(urlSearch).get('session') || saved.id || null);
  const ownerRef = useRef(null);
  const begin = useCallback((id, busy = false) => {
    pollRef.current?.();
    ownerRef.current?.controller.abort();
    const owner = { id, controller: new AbortController() };
    ownerRef.current = owner;
    setLoading(busy);
    setError(null);
    return owner;
  }, []);
  const current = useCallback(owner => Boolean(owner && ownerRef.current === owner && !owner.controller.signal.aborted), []);
  useLayoutEffect(() => () => { pollRef.current?.(); ownerRef.current?.controller.abort(); }, []);

  // Mirror pointer + drafts to sessionStorage on every change.
  useEffect(() => {
    writeStorage(SS_KEY, JSON.stringify(
      { id: session?.id || savedIdRef.current, q: query, qs: questions, tc: targetCollection, sort },
    ), 'sessionStorage');
  }, [session?.id, query, questions, targetCollection, sort, urlSearch]);

  const pollUntilDone = useCallback((owner, discovery) => {
    pollRef.current?.();
    pollRef.current = pollSessionUntilDone(owner.id, {
      fetchSession: async (id, options) => {
        const fresh = await getSession(id, options);
        if (fresh.id !== id) throw new Error('Session identity mismatch');
        return fresh;
      }, stopOnError: discovery,
      onSession: fresh => {
        if (!current(owner) || fresh.id !== owner.id) return;
        setSession(fresh); setError(null);
      },
      onError: error => { if (current(owner)) setError(error); },
    });
  }, [current]);

  const readSession = useCallback(async owner => {
    try {
      const fresh = await getSession(owner.id, { signal: owner.controller.signal });
      if (!current(owner)) return;
      if (fresh.id !== owner.id) throw new Error('Session identity mismatch');
      savedIdRef.current = fresh.id;
      setSession(fresh);
      if (fresh.status === 'reviewing') pollUntilDone(owner, hasDiscovery(fresh));
    } catch (error) {
      if (current(owner)) setError(error);
    } finally {
      if (current(owner)) setLoading(false);
    }
  }, [pollUntilDone, current]);

  useLayoutEffect(() => {
    const id = new URLSearchParams(urlSearch).get('session') || savedIdRef.current;
    if (!id || (ownerRef.current?.id === id && current(ownerRef.current))) return;
    savedIdRef.current = id;
    setSession(null);
    readSession(begin(id));
  }, [urlSearch, begin, readSession, current]);

  const materializeCard = async candidateId => {
    if (ownerRef.current?.id !== session?.id || loading || (hasDiscovery(session) && error)) return;
    const owner = hasDiscovery(session) ? begin(session.id, true) : ownerRef.current;
    try {
      const res = await materializeApi(owner.id, candidateId, targetCollection);
      if (!current(owner)) return;
      if (hasDiscovery(session)) await readSession(owner);
      else setSession(prev => prev?.id === owner.id ? { ...prev, candidates: prev.candidates.map(c =>
        c.candidate_id === candidateId ? { ...c, materialized_zotero_key: res.zotero_key } : c) } : prev);
    } catch (error) {
      if (current(owner)) {
        setLoading(false);
        if (hasDiscovery(session)) setError(error);
        throw error;
      }
    }
  };
  const review = async () => {
    const owner = begin(session.id, true);
    try {
      await startReview(owner.id);
      if (current(owner)) await readSession(owner);
    } catch (error) {
      if (current(owner)) { setError(error); setLoading(false); }
    }
  };

  const runScreen = useCallback(async (e) => {
    e?.preventDefault();
    if (!query.trim()) return;
    const owner = begin(null, true);
    savedIdRef.current = null;
    setSession(null);
    try {
      const qs = questions.split('\n').map((q) => q.trim()).filter(Boolean);
      const confirmed = Object.fromEntries(Object.entries(constraints).map(([key, value]) =>
        [key, value.split('\n').map((term) => term.trim()).filter(Boolean)]));
      const sess = await screenApi({ query: query.trim(), questions: qs,
        ...(Object.values(confirmed).some((values) => values.length) ? { constraints: confirmed } : {}) });
      if (!current(owner)) return;
      owner.id = sess.id;
      savedIdRef.current = sess.id;
      setSession(sess);
      onPointer?.(sess.id);
      if (sess.status === 'reviewing') pollUntilDone(owner, false);  // ordinary Search still auto-reviews
    } catch (err) {
      if (current(owner)) setError(err);
    } finally {
      if (current(owner)) setLoading(false);
    }
  }, [query, questions, constraints, begin, pollUntilDone, onPointer, current]);

  const discovery = hasDiscovery(session) ? session.discovery : null;
  const trusted = discovery?.freshness === 'current' && !loading && !error;
  const actionsAllowed = !discovery || Boolean(trusted && discovery.new_actions_allowed);
  const reviewing = session?.status === 'reviewing' && !error;
  // Doherty: name the honest stage. During agentic rounds refinements grow but no
  // review has landed yet — showing "Deep-reviewing…" then would mislabel the wait.
  const anyReviewed = (session?.candidates || []).some((c) => c.review && c.review.state);
  const refining = !discovery && reviewing && (session?.refinements || []).length > 0 && !anyReviewed;
  return (
    <div className="max-w-3xl mx-auto">
      <h1 className="text-lg font-semibold text-slate-900 mb-1">Targeted Search</h1>
      <p className="text-[12px] text-slate-500 mb-3">
        {discovery ? 'Discovery uses public metadata, not full-text quality. Review and Add require explicit actions.'
          : 'Describe a research topic. We plan a query per source, federate the open literature, dedupe version families, rank by real relevance, then deep-read the best few.'}
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
        <details className="text-[12px]">
          <summary>Explicit constraints (optional)</summary>
          <p>Only these confirmed filters are mandatory. One exact phrase per line; types require source metadata.
            Natural-language restrictions in the topic are proposals: confirm them here and search again.</p>
          {Object.entries({ must_include: 'Required exact phrases', must_not_include: 'Excluded exact phrases',
            study_types: 'Only publication types' }).map(([key, label]) => (
            <label key={key} className="block">{label}
              <textarea aria-label={label} rows={2} className="block border rounded p-1 w-full"
                value={constraints[key]} onChange={(e) => setConstraints({ ...constraints, [key]: e.target.value })} />
            </label>
          ))}
        </details>
        <button
          type="submit" disabled={loading || !query.trim()}
          className="justify-self-start px-4 py-1.5 rounded-lg bg-slate-900 text-white text-[13px] disabled:opacity-40"
        >
          {loading ? 'Searching…' : 'Search'}
        </button>
      </form>

      {loading && <p role="status">Waiting for the current request…</p>}
      {error && <><ErrorBanner error={humanizeError(error)} />
        <p>Status unavailable; the server may still be working. This is not a cancellation or successful completion.</p>
        {savedIdRef.current && <button type="button" onClick={() => readSession(begin(savedIdRef.current, hasDiscovery(session)))}>Retry session status</button>}
      </>}

      {session && (
        <div>
          {discovery && <>
            <DiscoveryStatus session={session} trusted={trusted} />
            <button type="button" className="mb-3 mr-2 rounded-lg border border-slate-300 px-3 py-3 text-sm text-forest-800 disabled:opacity-40" disabled={loading} onClick={() => readSession(begin(session.id, true))}>Refresh session status</button>
            <button type="button" className="mb-3 mr-2 rounded-lg border border-slate-300 px-3 py-3 text-sm text-forest-800 disabled:opacity-40" disabled={!actionsAllowed || reviewing || session.status !== 'discovery_ready'} onClick={review}>Review retained candidates</button>
          </>}
          {session.intent && session.intent.parse_ok === false && (
            <div className="mb-3 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-[12px] text-amber-800">
              Couldn’t structure this topic — searched your raw query as-is. Try rephrasing for a sharper plan.
            </div>
          )}
          <QueryPlan plan={session.plan || {}} />
          {!discovery && <Refinements rounds={session.refinements} />}
          <div className="flex items-center justify-between gap-2 mb-2 flex-wrap">
            <div className="text-[12px] text-slate-500">{(session.candidates || []).length} candidates</div>
            {!discovery && <label className="text-[12px] text-slate-600">
              Sort by{' '}
              <select value={sort} onChange={(e) => setSort(e.target.value)} className="rounded border border-slate-300 p-1">
                <option value="recommended">Recommended</option>
                <option value="relevance">Relevance</option>
                <option value="relevance_prestige">Relevance + prestige</option>
              </select>
            </label>}
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
          {!discovery && sort === 'relevance_prestige' && <p className="mb-2 text-[12px] text-slate-500">85% relevance + 15% citation prestige (log-scaled within these results; unknown citations use the median). Citation counts favor older papers and vary by field.</p>}
          {(() => {
            const cands = discovery ? session.candidates || [] : sortSearchCandidates(session.candidates || [], sort);
            const recommended = trusted ? discovery.recommended_ids.slice(0, 3) : [];
            const strong = discovery ? recommended.map(id => cands.find(c => c.candidate_id === id)).filter(Boolean)
              : sort === 'recommended' ? cands.filter(c => c.relevance_band !== 'weak') : cands;
            const weak = discovery ? cands.filter(c => !recommended.includes(c.candidate_id))
              : sort === 'recommended' ? cands.filter(c => c.relevance_band === 'weak') : [];
            const card = c => <CandidateCard key={`${session.id}:${c.candidate_id}`} cand={c} onAdd={materializeCard}
              discovery={discovery} trusted={trusted}
              actionsAllowed={!loading && (!discovery || !error) && (actionsAllowed || Boolean(c.planned_zotero_key))} />;
            return (
              <>
                <div className="grid gap-3">
                  {strong.map(card)}
                </div>
                {weak.length > 0 && (
                  <details className="mt-3">
                    <summary className="cursor-pointer text-[12px] text-slate-500 font-semibold py-1">
                      {discovery ? `${weak.length} retained candidates` : `${weak.length} weaker match${weak.length === 1 ? '' : 'es'}`} ▾
                    </summary>
                    <div className="grid gap-3 mt-2">
                      {weak.map(card)}
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
