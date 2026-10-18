// @vitest-environment jsdom
import React from 'react';
import { cleanup, render, within } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import PaperReview from './PaperReview.jsx';

afterEach(cleanup);

const digest = {
  read_decision: 'skim', read_why: 'Inspect the held-out evaluation before designing our experiment.',
  read_parts: ['§4.2 Held-out evaluation — compare site splits', 'Figure 3 — inspect failure cases'],
  skip_parts: ['Appendix A — notation recap'], estimated_read_minutes: 12,
  original_value: 'The site-split protocol and individual failure examples.',
  tldr: 'A synthetic evaluation method.',
};

it.each([{}, { compact: true }, { flat: true }])('makes the ordered reading plan visible once on surface %j', (surface) => {
  const { container, getByRole } = render(<PaperReview {...surface} deep={{ digest }} />);
  const plan = getByRole('region', { name: 'Reading plan' });

  expect(plan.closest('details')).toBeNull();
  expect(plan.classList.contains('[&_dt]:text-slate-600')).toBe(true);
  expect(within(plan).getByRole('list', { name: 'Read parts' }).tagName).toBe('OL');
  expect(within(plan).getAllByRole('listitem').map(el => el.textContent)).toEqual([
    ...digest.read_parts, ...digest.skip_parts,
  ]);
  for (const text of [...digest.read_parts, ...digest.skip_parts, digest.original_value, digest.read_why]) {
    expect(container.textContent.split(text).length - 1, text).toBe(1);
  }
  expect(within(plan).getByText(digest.original_value).closest('details')).toBeNull();
  expect(container.textContent).toContain('12 min');
});

it('does not turn absent optional fields in an old cache into zero time or made-up targets', () => {
  const { container, getByRole } = render(<PaperReview deep={{ digest: {
    read_decision: 'skim', read_why: 'A saved rationale.', tldr: 'A saved contribution.',
  } }} />);
  const plan = getByRole('region', { name: 'Reading plan' });

  expect(container.textContent).toContain('Time not estimated');
  expect(plan.textContent).toContain('Exact reading targets not recorded');
  expect(plan.textContent).toContain('Additional value not recorded');
  expect(within(plan).queryByRole('list')).toBeNull();
  expect(container.textContent).not.toContain('0 min');
});

it('keeps digest-enough self-contained without reviving inconsistent cached read targets', () => {
  const { container, getByRole } = render(<PaperReview deep={{ digest: { ...digest, read_decision: 'skip',
    read_why: 'The digest captures the useful result; the original adds little now.', estimated_read_minutes: 0,
  } }} />);
  const plan = getByRole('region', { name: 'Reading plan' });

  expect(container.textContent).toContain('DIGEST IS ENOUGH');
  expect(container.textContent).toContain('0 min');
  expect(container.textContent).toContain('the original adds little now');
  expect(plan.textContent).toContain(digest.skip_parts[0]);
  expect(container.textContent).not.toContain(digest.read_parts[0]);
  expect(container.textContent).not.toContain(digest.original_value);
});

it.each([
  ['', {}], [undefined, { compact: true }], ['legacy', { flat: true }],
])('withholds stale action advice for an unknown decision (%s, %j)', (decision, surface) => {
  const { container, queryByRole } = render(<PaperReview {...surface} deep={{
    digest: { ...digest, read_decision: decision },
  }} />);

  expect(container.textContent).toContain('REVIEW');
  expect(container.textContent).toContain('Reading decision unavailable');
  expect(container.textContent).not.toContain(digest.read_why);
  expect(queryByRole('region', { name: 'Reading plan' })).toBeNull();
  expect(container.textContent).not.toContain('12 min');
  expect(container.textContent).not.toContain(digest.read_parts[0]);
});

it('uses the projected abstract action, never the raw model proposal', () => {
  const { container, getByRole } = render(<PaperReview deep={{
    digest: { ...digest, basis: 'abstract', read_decision: 'skim' },
    model_read_decision: 'read', reading_policy_flags: ['abstract_only'],
  }} />);

  expect(container.textContent).toContain('SKIM');
  expect(within(container).queryByText('READ', { exact: true })).toBeNull();
  expect(getByRole('region', { name: 'Reading plan' }).textContent).toContain(digest.read_parts[0]);
});

it('preserves read plans and literal untrusted text without HTML or invented links', () => {
  const target = '§3 <script>alert("source")</script> & methods';
  const { container, getByRole } = render(<PaperReview deep={{ digest: { ...digest,
    read_decision: 'read', read_parts: [target],
  } }} />);
  const plan = getByRole('region', { name: 'Reading plan' });

  expect(plan.textContent).toContain(target);
  expect(plan.querySelector('script')).toBeNull();
  expect(plan.querySelector('a')).toBeNull();
  expect(container.textContent).toContain('READ');
});
