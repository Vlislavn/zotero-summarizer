const STATES = {
  sufficient: 'Sufficient evidence (AI-verified)', partial: 'Partial evidence',
  not_found_in_checked_text: 'Not found in checked text', unknown: 'Unchecked',
};

export function EvidencePolicyStatus({ session }) {
  if (session.evidence_policy !== 'question_evidence') return null;
  const status = session.evidence_status || {};
  return <div role="status" className="mb-3 text-[12px] text-slate-600">
    Experimental question evidence: {status.state || 'waiting'}.
    {status.reason && <span> {status.reason.replaceAll('_', ' ')}.</span>}
    {status.state === 'complete' && <span> {status.promoted_candidate_ids?.length || 0} candidates promoted;
      {' '}{status.calls_used} / {status.calls} client calls used. Topic floor: {status.topic_floor}.</span>}
    {' '}Only sufficient original-text answers can promote; unchecked evidence is not a negative judgment.
    {status.state === 'unavailable' && <span> Baseline order is preserved.</span>}
    {' '}Quality and latency gains are unmeasured. Add to library remains manual.
  </div>;
}

export default function QuestionEvidence({ candidate, questions = [] }) {
  if (!questions.length) return null;
  return <details className="mt-3 border-t border-slate-200 pt-2 text-[12px] text-slate-700">
    <summary className="cursor-pointer font-medium">Question evidence</summary>
    <p>Only retrieved original passages checked; extraction completeness and the remainder are unchecked.
      AI support judgments are not scientific truth.</p>
    <ol className="grid gap-2 mt-2">
      {questions.map((question, index) => {
        const row = (candidate.question_evidence || []).find((r) => r.question_index === index
          && r.question === question && r.candidate_id === candidate.candidate_id);
        return <li key={index}>
          <div className="font-medium break-words">{question}</div>
          <div>{STATES[row?.state] || STATES.unknown}</div>
          {row?.reason !== 'verified' && <p>{row ? row.reason.replaceAll('_', ' ') : 'Not assessed'}.
            {' '}Unavailable or missing passages do not establish absence.</p>}
          {row?.error_type && <p>Error: {row.error_code || row.error_type}</p>}
          {row?.answer && <p>{row.answer}</p>}
          {row?.uncovered?.length > 0 && <p>Uncovered: {row.uncovered.join('; ')}</p>}
          {(row?.evidence || []).map((e) => <blockquote key={e.passage_id} className="border-l-2 border-slate-300 pl-2 mt-1 break-words">
            {e.quote}<cite className="block not-italic">Extracted characters {e.start}–{e.end} · {e.passage_id}</cite>
          </blockquote>)}
          {row?.source_sha256 && <details className="mt-1 break-all">
            <summary>Source and evaluator provenance</summary>
            <p>Source: {row.source_id} · Extraction SHA-256: {row.source_sha256}</p>
            <p>Question SHA-256: {row.question_id} · Input: {row.input_id}</p>
            <p>Generator: {row.generator_id} · Verifier: {row.verifier_id} · Protocol: {row.evaluator_id}</p>
            <p>Dense: {String(row.capabilities?.dense)} · BM25: {String(row.capabilities?.bm25)}
              {' '}· Reranker: {String(row.capabilities?.reranker)}</p>
            <p>Retrieval models: {row.capabilities?.embedder_model} · {row.capabilities?.reranker_model}</p>
            <p>Extraction extent (not checked coverage): {row.checked_scope?.start}–{row.checked_scope?.end}.
              {' '}Checked passages: {(row.checked_passages || []).map((p) => `${p.start}–${p.end}`).join(', ')}.</p>
          </details>}
        </li>;
      })}
    </ol>
  </details>;
}
