// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import VerdictPanel from './VerdictPanel.jsx';
afterEach(cleanup);

it('keeps an edited verdict and reason when the same paper refreshes', () => {
  const onSubmit = vi.fn();
  const { rerender } = render(<VerdictPanel itemKey="P1" existingVerdict={{ user_priority: 'must_read', comment: 'Initial' }} onSubmit={onSubmit} />);
  fireEvent.click(screen.getByRole('button', { name: 'Edit', exact: true }));
  fireEvent.click(screen.getByRole('button', { name: 'Could read', exact: true }));
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Device draft' } });
  rerender(<VerdictPanel itemKey="P1" existingVerdict={{ user_priority: 'should_read', comment: 'Changed elsewhere' }} onSubmit={onSubmit} />);
  expect(screen.getByRole('textbox').value).toBe('Device draft');
  fireEvent.click(screen.getByRole('button', { name: 'Update', exact: true }));
  expect(onSubmit).toHaveBeenCalledWith({ user_priority: 'could_read', comment: 'Device draft' });
});

it('keeps a newer reason when an earlier explicit save is acknowledged', () => {
  const onSubmit = vi.fn();
  const { rerender } = render(<VerdictPanel itemKey="P1" onSubmit={onSubmit} />);
  fireEvent.click(screen.getByRole('button', { name: 'Should read', exact: true }));
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'First saved reason' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save verdict', exact: true }));
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Newer draft reason' } });
  rerender(<VerdictPanel itemKey="P1" existingVerdict={{ user_priority: 'should_read', comment: 'First saved reason' }} onSubmit={onSubmit} />);
  expect(screen.getByRole('textbox').value).toBe('Newer draft reason');
  fireEvent.click(screen.getByRole('button', { name: 'Update', exact: true }));
  expect(onSubmit).toHaveBeenLastCalledWith({ user_priority: 'should_read', comment: 'Newer draft reason' });
});
