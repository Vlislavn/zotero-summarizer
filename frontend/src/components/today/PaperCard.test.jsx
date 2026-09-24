// @vitest-environment jsdom
import { afterEach, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import PaperCard from './PaperCard.jsx';

afterEach(cleanup);

it('explains the no-LLM Add boundary while leaving the source readable', () => {
  render(<MemoryRouter><PaperCard paper={{ item_id: 1, title: 'Offline paper',
    stable_feed_key: 'feed:g:paper', composite_score: 3, review_ready: false }}
    llmEnabled={false} selected={false} onToggleSelect={() => {}} /></MemoryRouter>);
  expect(screen.getByText(/AI review is off/)).toBeTruthy();
  expect(screen.getByRole('link', { name: 'Enable AI' }).getAttribute('href')).toBe('/settings');
  expect(screen.getByRole('link', { name: /Open full review/ })).toBeTruthy();
  expect(screen.queryByText('Generate a review before adding.')).toBeNull();
});

it('does not attribute an unknown zero max h-index to the first author', () => {
  render(<PaperCard
    paper={{
      item_id: 'paper-1',
      title: 'Mixture of Mini Experts',
      authors: 'Faisal Mahmood',
      max_author_h_index: 0,
      composite_score: 3,
    }}
    selected={false}
    onToggleSelect={() => {}}
  />);

  expect(screen.getByText('Faisal Mahmood')).toBeTruthy();
  expect(screen.queryByText('(h=0)')).toBeNull();
});
