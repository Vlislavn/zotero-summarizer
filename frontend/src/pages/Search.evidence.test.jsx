// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Link, MemoryRouter } from 'react-router-dom';
import Search from './Search.jsx';

const question = 'Did the treatment improve the outcome?';
const receipt = {
  candidate_id: 'answer', question_index: 0, question, question_id: 'question-fingerprint',
  state: 'sufficient', reason: 'verified', answer: 'No improvement was observed.',
  source_kind: 'acquired_original', complete: false, source_id: 'original-identity',
  source_version_id: 'extraction-version', source_sha256: 'source-fingerprint',
  generator_id: 'configured-feed-generator', verifier_id: 'configured-deep-verifier',
  checked_scope: { start: 0, end: 3000 },
  evidence: [{ passage_id: 'chars:1500:1600', start: 1500, end: 1600, quote: 'The treatment did not improve the outcome.' }],
};

afterEach(() => { cleanup(); sessionStorage.clear(); vi.unstubAllGlobals(); });

function savedSession(overrides = {}) {
  const session = {
    id: 'evidence', status: 'reviewed', questions: [question], evidence_policy: 'question_evidence',
    evidence_status: { state: 'complete', promoted_candidate_ids: ['answer'], topic_floor: 0.35 },
    candidates: [
      { candidate_id: 'answer', title: 'Lower-ranked answer', url: 'https://example.org/answer', query_score: 0.5,
        relevance_band: 'weak', question_evidence: [receipt] },
      { candidate_id: 'topic', title: 'Top terminology match', url: 'https://example.org/topic', query_score: 0.95 },
    ], ...overrides,
  };
  sessionStorage.setItem('zs.searchSession', JSON.stringify({ id: session.id }));
  vi.stubGlobal('fetch', vi.fn(async (path) => new Response(JSON.stringify(
    path === '/api/search/evidence' ? session : { items: [] },
  ))));
  return session;
}

it('keeps a server-promoted weak answer visible in the recommended order', async () => {
  savedSession();
  render(<Search />);
  await screen.findByText('Lower-ranked answer');
  expect(screen.queryByText(/weaker match/)).toBeNull();
  expect(screen.getAllByRole('link').filter((link) => link.target === '_blank').map((link) => link.textContent))
    .toEqual(['Lower-ranked answer', 'Top terminology match']);
  const card = screen.getByText('Lower-ranked answer').closest('.border.rounded-xl');
  fireEvent.click(within(card).getByText('Question evidence'));
  expect(within(card).getByText('Sufficient evidence (AI-verified)')).toBeTruthy();
  expect(within(card).getByText('The treatment did not improve the outcome.')).toBeTruthy();
  expect(within(card).getByText(/Extracted characters 1500–1600/)).toBeTruthy();
  expect(within(card).getByText(/Only retrieved original passages checked/)).toBeTruthy();
  fireEvent.click(within(card).getByText('Source and evaluator provenance'));
  expect(within(card).getByText(/Verifier: configured-deep-verifier/)).toBeTruthy();
  expect(within(card).getByText(/Question SHA-256: question-fingerprint/)).toBeTruthy();
  expect(screen.getAllByText('Unchecked')).toHaveLength(1);
  expect(fetch.mock.calls.some(([path]) => path.includes('materialize'))).toBe(false);
});

it('submits evidence policy only after explicit selection and with questions', async () => {
  const bodies = [];
  vi.stubGlobal('fetch', vi.fn(async (path, options = {}) => {
    if (path === '/api/search/screen') {
      bodies.push(JSON.parse(options.body));
      return new Response(JSON.stringify({ id: 'new-search', status: 'reviewed', candidates: [] }));
    }
    return new Response(JSON.stringify({ items: [] }));
  }));
  render(<Search />);
  fireEvent.change(screen.getByPlaceholderText(/LLM agents for clinical decision support/), { target: { value: 'treatment studies' } });
  fireEvent.change(screen.getByPlaceholderText(/one specific question per line/), { target: { value: question } });
  fireEvent.click(screen.getByRole('button', { name: 'Search' }));
  await waitFor(() => expect(bodies).toHaveLength(1));
  expect(bodies[0].evidence_policy).toBeUndefined();
  fireEvent.click(screen.getByRole('checkbox', { name: /Experimental original-source question evidence/ }));
  fireEvent.click(screen.getByRole('button', { name: 'Search' }));
  await waitFor(() => expect(bodies).toHaveLength(2));
  expect(bodies[1].evidence_policy).toBe('question_evidence');
  expect(screen.getByRole('checkbox', { name: /Experimental original-source question evidence/ }).checked).toBe(false);
  fireEvent.change(screen.getByPlaceholderText(/one specific question per line/), { target: { value: 'Did the control group improve?' } });
  fireEvent.click(screen.getByRole('button', { name: 'Search' }));
  await waitFor(() => expect(bodies).toHaveLength(3));
  expect(bodies[2].evidence_policy).toBeUndefined();
  fireEvent.change(screen.getByPlaceholderText(/one specific question per line/), { target: { value: '' } });
  fireEvent.click(screen.getByRole('button', { name: 'Search' }));
  await waitFor(() => expect(bodies).toHaveLength(4));
  expect(bodies[3].evidence_policy).toBeUndefined();
});

it.each(['failed', 'stale'])('preserves current evidence consent and draft after %s creation', async outcome => {
  const bodies = [];
  let finish;
  const pending = new Promise(resolve => { finish = resolve; });
  vi.stubGlobal('fetch', vi.fn(async (path, options = {}) => {
    if (path === '/api/search/screen') {
      bodies.push(JSON.parse(options.body));
      return bodies.length === 1 ? pending : new Response(JSON.stringify({ id: 'retry', status: 'reviewed', candidates: [] }));
    }
    return new Response(JSON.stringify(path === '/api/search/kept'
      ? { id: 'kept', status: 'reviewed', candidates: [{ candidate_id: 'kept', title: 'Retained current result' }] }
      : { items: [] }));
  }));
  render(<MemoryRouter initialEntries={['/search']}>
    <Link to="/search?session=kept">Open retained session</Link><Search />
  </MemoryRouter>);
  fireEvent.change(screen.getByLabelText('Search query'), { target: { value: 'treatment studies' } });
  fireEvent.change(screen.getByLabelText('Questions (optional)'), { target: { value: question } });
  fireEvent.click(screen.getByRole('checkbox', { name: /Experimental original-source question evidence/ }));
  fireEvent.click(screen.getByRole('button', { name: 'Search', exact: true }));
  await waitFor(() => expect(bodies).toHaveLength(1));
  expect(bodies[0].evidence_policy).toBe('question_evidence');
  const draft = 'Did the control group improve?';
  fireEvent.change(screen.getByLabelText('Questions (optional)'), { target: { value: draft } });
  if (outcome === 'stale') {
    fireEvent.click(screen.getByRole('link', { name: 'Open retained session' }));
    await screen.findByText('Retained current result');
  }
  await act(async () => finish(new Response(JSON.stringify(outcome === 'failed'
    ? { error: 'creation_failed' } : { id: 'obsolete', status: 'reviewed', candidates: [] }),
  { status: outcome === 'failed' ? 503 : 200 })));
  await waitFor(() => expect(screen.getByRole('button', { name: 'Search', exact: true }).disabled).toBe(false));
  expect(screen.getByRole('checkbox', { name: /Experimental original-source question evidence/ }).checked).toBe(true);
  expect(screen.getByLabelText('Questions (optional)').value).toBe(draft);
  expect(screen.getByLabelText('Search query').value).toBe('treatment studies');
  if (outcome === 'stale') {
    expect(screen.getByText('Retained current result')).toBeTruthy();
    expect(JSON.parse(sessionStorage.getItem('zs.searchSession')).id).toBe('kept');
  }
  fireEvent.click(screen.getByRole('button', { name: 'Search', exact: true }));
  await waitFor(() => expect(bodies).toHaveLength(2));
  expect(bodies[1].questions).toEqual([draft]);
  expect(bodies[1].evidence_policy).toBe('question_evidence');
  await waitFor(() => expect(screen.getByRole('checkbox', { name: /Experimental original-source question evidence/ }).checked).toBe(false));
});

it('distinguishes unavailable routing, partial evidence and retrieval misses from absence', async () => {
  savedSession({ evidence_status: { state: 'unavailable', reason: 'separate_generator_verifier_required' },
    candidates: [
      { candidate_id: 'answer', title: 'Partial', question_evidence: [{ ...receipt, state: 'partial', uncovered: ['second outcome'] }] },
      { candidate_id: 'miss', title: 'Unchecked', question_evidence: [
        { ...receipt, candidate_id: 'miss', state: 'unknown', reason: 'unchecked_remainder', answer: undefined, evidence: [] },
      ] },
    ] });
  render(<Search />);
  expect(await screen.findByText('Partial')).toBeTruthy();
  expect(screen.getByRole('status').textContent).toContain('Baseline order is preserved');
  expect(screen.getByRole('status').textContent).toContain('separate generator verifier required');
  expect(screen.getByText('Partial evidence')).toBeTruthy();
  expect(screen.getByText('Uncovered: second outcome')).toBeTruthy();
  expect(screen.getByText(/Unavailable or missing passages do not establish absence/)).toBeTruthy();
  expect(screen.queryByText('Not found in checked text')).toBeNull();
});
