// @vitest-environment jsdom
import { afterEach, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import QuestionEvidence, { EvidencePolicyStatus } from './QuestionEvidence.jsx';
import { reviewPaperUrl } from '../../pages/reviewHelpers.js';

afterEach(cleanup);

it.each(['PMC123456', '123456'])('links PMCID-only discovery results canonically: %s', (pmcid) => {
  expect(reviewPaperUrl({ pmcid })).toBe('https://pmc.ncbi.nlm.nih.gov/articles/PMC123456/');
  expect(reviewPaperUrl({ doi: '10.1234/doi', pmcid })).toBe('https://doi.org/10.1234/doi');
  expect(reviewPaperUrl({ url: 'https://example.org/direct', pmcid })).toBe('https://example.org/direct');
  expect(reviewPaperUrl({ pmcid: 'PMC123/../../private' })).toBe('');
});

it.each(['https://example.org/selected.pdf', 'http://example.org/selected.pdf', 'file:///private/cache.pdf', 'javascript:alert(1)', undefined])(
  'only exposes recorded HTTP sources inside the existing provenance: %s', (sourceUri) => {
    render(<QuestionEvidence questions={['Question']} candidate={{ candidate_id: 'paper', question_evidence: [{
      candidate_id: 'paper', question_index: 0, question: 'Question', state: 'unknown', reason: 'not_assessed',
      source_sha256: 'synthetic-extraction', source_uri: sourceUri,
    }] }} />);
    const link = screen.queryByRole('link', { name: 'Open acquired original source', hidden: true });
    if (sourceUri?.startsWith('http')) {
      expect(link?.getAttribute('href')).toBe(sourceUri);
      expect(link?.getAttribute('rel')).toBe('noopener noreferrer');
      expect(link?.closest('details')?.querySelector('summary')?.textContent).toBe('Source and evaluator provenance');
    } else expect(link).toBeNull();
  },
);

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
