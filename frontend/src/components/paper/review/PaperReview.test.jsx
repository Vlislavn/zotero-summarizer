import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import PaperReview from './PaperReview.jsx';

describe('withheld reading decision', () => {
  it.each([null, { read_decision: '', read_why: 'Goals were not assessed.' }])(
    'does not reconstruct SKIP from an unknown goal board (%j)', (digest) => {
      const html = renderToStaticMarkup(<PaperReview deep={{
        digest,
        quality: { quality_band: 'neutral' },
        goal_summaries: [{ goal: 'Evaluation', retrieval_state: 'not_retrieved', abstained: true }],
      }} />);

      expect(html).toContain('REVIEW');
      expect(html).not.toContain('none of your research goals are addressed');
      expect(html).not.toContain('>SKIP<');
    },
  );
});
