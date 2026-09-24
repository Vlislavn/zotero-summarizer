// @vitest-environment jsdom
import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import StepProgress from './StepProgress.jsx';

describe('StepProgress accessibility', () => {
  it('announces the current step and labels completed steps without relying on color', () => {
    render(<StepProgress current={1} validity={[true, true, false]} maxReached={1} />);

    const steps = screen.getByRole('list', { name: 'Setup progress' });
    const current = within(steps).getByRole('listitem', { current: 'step' });
    expect(current.textContent).toContain('Connect LLM');
    expect(within(steps).getByText('Zotero sync').parentElement.textContent).toContain('Complete');
    expect(within(steps).getByText('Describe research').parentElement.textContent).toContain('Not complete');
  });
});
