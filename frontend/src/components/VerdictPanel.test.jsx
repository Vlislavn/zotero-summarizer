// @vitest-environment jsdom
import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import VerdictPanel from './VerdictPanel.jsx';

it('discards an edit on Cancel and preserves the saved comment on a later update', () => {
  const onSubmit = vi.fn();
  const existingVerdict = {
    id: 7, item_key: 'P1', user_priority: 'should_read', comment: 'Keep this rationale',
  };
  render(<VerdictPanel itemKey="P1" existingVerdict={existingVerdict} onSubmit={onSubmit} />);

  fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
  fireEvent.click(screen.getByRole('button', { name: 'Could read' }));
  fireEvent.change(screen.getByPlaceholderText(/^Why\?/), { target: { value: 'temporary draft' } });
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
  expect(screen.getByText(/Keep this rationale/)).toBeTruthy();
  expect(screen.queryByPlaceholderText(/^Why\?/)).toBeNull();
  expect(onSubmit).not.toHaveBeenCalled();

  fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
  fireEvent.click(screen.getByRole('button', { name: 'Must read' }));
  fireEvent.click(screen.getByRole('button', { name: 'Update' }));
  expect(onSubmit).toHaveBeenCalledWith({
    user_priority: 'must_read', comment: 'Keep this rationale',
  });
});
