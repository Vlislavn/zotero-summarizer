const STATE_LABELS = {
  sufficient: 'Sufficient evidence (AI-verified)',
  partial: 'Partial evidence',
  not_found_in_checked_text: 'Not found in checked text',
  unknown: 'Unchecked',
};

const REASONS = {
  unchecked_remainder: 'No answer located in retrieved passages; the rest is unchecked.',
  no_passages_retrieved: 'No passages retrieved; this is not evidence of absence.',
  call_budget_exhausted: 'Evidence work budget reached.',
  source_unavailable: 'Original text unavailable.',
  source_empty: 'No original text extracted.',
  source_partial: 'Only partial original text available.',
  source_truncated: 'Original text was truncated.',
  source_character_budget_exceeded: 'Original text exceeds the evidence work budget.',
  generator_unavailable: 'Answer generator unavailable.',
  verifier_unavailable: 'Evidence verifier unavailable.',
  verifier_unknown: 'The verifier could not establish support.',
};

export function EvidencePolicyStatus({ session }) {
  if (session.evidence_policy !== 'question_evidence') return null;
  const status = session.evidence_status || {};
  const unavailable = status.state === 'unavailable';
  const message = status.reason === 'separate_generator_verifier_required'
    ? 'Choose different Feed (generator) and Deep review (verifier) model routes in Settings.'
    : status.reason === 'evidence_budget_disabled'
      ? 'The operator disabled the evidence work budget.'
      : 'Evidence prerequisites are unavailable.';
  return (
    <p role="status" className="mb-3 text-[12px] text-slate-600">
      {unavailable ? <>Question evidence unavailable. {message} Baseline review order is preserved.{' '}
        <a className="underline" href="/settings#ai-models">AI settings</a></>
        : status.state === 'complete'
          ? <>Experimental question evidence: {status.promoted_candidate_ids?.length || 0} answer-bearing candidates promoted.
            Only verified sufficient answers above the topic floor can change review order. Unknown is not a negative.</>
          : <>Checking original-text evidence before review selection… Results update as passages are checked.</>}
      {' '}Not a measured quality or latency improvement; adding to Zotero is always manual.
    </p>
  );
}

export default function QuestionEvidence({ candidate, questions = [] }) {
  if (!questions.length) return null;
  return (
    <details className="mt-3 border-t border-slate-200 pt-2 text-[12px] text-slate-700">
      <summary className="cursor-pointer font-medium">Question evidence</summary>
      <p className="mt-1 text-slate-600">AI judgments concern the located passages, not scientific truth or a whole-document search.</p>
      <ol className="mt-2 grid gap-3">
        {questions.map((question, index) => {
          const receipt = (candidate.question_evidence || []).find((row) => row.question_index === index
            && row.question === question && row.candidate_id === candidate.candidate_id);
          const state = receipt?.state || 'unknown';
          return (
            <li key={index}>
              <div className="font-medium break-words">{question}</div>
              <div>{STATE_LABELS[state] || STATE_LABELS.unknown}</div>
              {receipt?.reason !== 'verified' && <p>{REASONS[receipt?.reason] || (receipt ? 'Evidence could not be validated.' : 'Not assessed.')}</p>}
              {receipt?.answer && <p className="mt-1">{receipt.answer}</p>}
              {receipt?.uncovered?.length > 0 && <p>Still uncovered: {receipt.uncovered.join('; ')}</p>}
              {receipt?.source_kind === 'acquired_original' && <p className="mt-1 text-slate-600">
                Only retrieved passages checked; extraction completeness is unknown. Other text remains unchecked.
              </p>}
              {(receipt?.evidence || []).map((passage) => (
                <blockquote key={passage.passage_id} className="mt-2 border-l-2 border-slate-300 pl-2 break-words">
                  <p>{passage.quote}</p>
                  <cite className="not-italic text-slate-600">Extracted characters {passage.start}–{passage.end} · {passage.passage_id}</cite>
                </blockquote>
              ))}
              {receipt?.source_sha256 && <details className="mt-1">
                <summary className="cursor-pointer">Source and evaluator provenance</summary>
                <dl className="mt-1 break-all">
                  <dt>Original source identity</dt><dd>{receipt.source_id}</dd>
                  <dt>Extraction version / SHA-256</dt><dd>{receipt.source_version_id} / {receipt.source_sha256}</dd>
                  <dt>Question SHA-256</dt><dd>{receipt.question_id}</dd>
                  <dt>Generator</dt><dd>{receipt.generator_id}</dd>
                  <dt>Verifier</dt><dd>{receipt.verifier_id}</dd>
                  <dt>Checked extraction extent (not whole-paper coverage)</dt>
                  <dd>{receipt.checked_scope?.start}–{receipt.checked_scope?.end}</dd>
                </dl>
              </details>}
            </li>
          );
        })}
      </ol>
    </details>
  );
}
