// @vitest-environment jsdom
import { afterEach, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';

import PaperCard from './PaperCard.jsx';

afterEach(cleanup);

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
