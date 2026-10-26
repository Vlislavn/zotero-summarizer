// @vitest-environment jsdom
import { afterEach, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import QuestionEvidence, { EvidencePolicyStatus } from './QuestionEvidence.jsx';

afterEach(cleanup);

it('shows scoped partial evidence, original coordinates, capability breakdown and evaluator identity', () => {
  render(<QuestionEvidence questions={['Question A', 'Question B']} candidate={{ candidate_id: 'paper', question_evidence: [{
    candidate_id: 'paper', question_index: 0, question: 'Question A', state: 'partial', reason: 'verified',
    answer: 'Synthetic answer', uncovered: ['Missing comparison'], source_id: 'source-hash', source_sha256: 'extraction-hash',
    question_id: 'question-hash', input_id: 'input-hash', generator_id: 'generator', verifier_id: 'verifier', evaluator_id: 'protocol-hash',
    capabilities: { dense: false, bm25: true, reranker: false, embedder_model: 'dense-model', reranker_model: 'rank-model' },
    checked_scope: { start: 0, end: 1200 }, checked_passages: [{ start: 300, end: 900 }],
    evidence: [{ passage_id: 'chars:401:420', start: 401, end: 420, quote: 'Original quotation.' }],
  }] }} />);
  expect(screen.getByText('Partial evidence')).toBeTruthy();
  expect(screen.getByText('Uncovered: Missing comparison')).toBeTruthy();
  expect(screen.getByText('Original quotation.')).toBeTruthy();
  expect(screen.getByText(/Extracted characters 401–420/)).toBeTruthy();
  expect(screen.getByText(/Dense: false · BM25: true/)).toBeTruthy();
  expect(screen.getByText(/Protocol: protocol-hash/)).toBeTruthy();
  expect(screen.getByText('Unchecked')).toBeTruthy();
  expect(screen.getByText(/Only retrieved original passages/)).toBeTruthy();
});

it('does not display another candidate or old question receipt', () => {
  render(<QuestionEvidence questions={['New question']} candidate={{ candidate_id: 'current', question_evidence: [
    { candidate_id: 'other', question_index: 0, question: 'New question', state: 'sufficient', answer: 'Wrong candidate' },
    { candidate_id: 'current', question_index: 0, question: 'Old question', state: 'sufficient', answer: 'Old answer' },
  ] }} />);
  expect(screen.getByText('Unchecked')).toBeTruthy();
  expect(screen.queryByText('Wrong candidate')).toBeNull();
  expect(screen.queryByText('Old answer')).toBeNull();
});

it('keeps unavailable distinct from scientific rejection and hides policy UI in baseline', () => {
  const page = render(<EvidencePolicyStatus session={{ evidence_policy: 'baseline' }} />);
  expect(screen.queryByRole('status')).toBeNull();
  page.rerender(<EvidencePolicyStatus session={{ evidence_policy: 'question_evidence', evidence_status: {
    state: 'unavailable', reason: 'separate_generator_verifier_required',
  } }} />);
  expect(screen.getByRole('status').textContent).toContain('Baseline order is preserved');
  expect(screen.getByRole('status').textContent).toContain('unmeasured');
});
