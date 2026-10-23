// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import ReviewNotes from './ReviewNotes.jsx';

const api = vi.hoisted(() => ({ saveReviewNote: vi.fn() }));
vi.mock('../../../api/goldenApi.js', () => ({ saveReviewNote: api.saveReviewNote }));
beforeEach(() => { api.saveReviewNote.mockReset(); });
afterEach(cleanup);

it('keeps an edited draft when canonical props refresh for the same paper', () => {
  const { rerender } = render(<ReviewNotes itemKey="P1" initialNote="Server baseline" />);
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Unsaved device judgement' } });
  rerender(<ReviewNotes itemKey="P1" initialNote="Changed elsewhere" />);
  expect(screen.getByRole('textbox').value).toBe('Unsaved device judgement');
  expect(screen.getByRole('button', { name: 'Save', exact: true }).disabled).toBe(false);
});

it('does not overwrite a newer draft when the saved older text is refreshed', async () => {
  let finish;
  api.saveReviewNote.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
  const onSaved = vi.fn();
  const { rerender } = render(<ReviewNotes itemKey="P1" initialNote="Baseline" onSaved={onSaved} />);
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'First saved text' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save', exact: true }));
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Newer unsaved text' } });
  await act(async () => { finish({ saved_offline: true }); });
  expect(onSaved).toHaveBeenCalledOnce();
  rerender(<ReviewNotes itemKey="P1" initialNote="First saved text" onSaved={onSaved} />);
  expect(screen.getByRole('textbox').value).toBe('Newer unsaved text');
  expect(screen.getByRole('button', { name: 'Save', exact: true }).disabled).toBe(false);
});

it('ignores an old paper save completion after the editor changes owner', async () => {
  let finish;
  api.saveReviewNote.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
  const onSaved = vi.fn();
  const { rerender } = render(<ReviewNotes itemKey="P1" initialNote="First baseline" onSaved={onSaved} />);
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'First saved text' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save', exact: true }));
  rerender(<ReviewNotes itemKey="P2" initialNote="Other paper" onSaved={onSaved} />);
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Other draft' } });
  await act(async () => { finish({ saved_offline: true }); });
  expect(screen.getByRole('textbox').value).toBe('Other draft');
  expect(onSaved).not.toHaveBeenCalled();
  expect(screen.queryByText('Saved on this device — waiting to sync.', { exact: true })).toBeNull();
});

it('leaves failed-save text editable and does not call it acknowledged', async () => {
  api.saveReviewNote.mockRejectedValue(new Error('Device storage quota exceeded'));
  const onSaved = vi.fn();
  render(<ReviewNotes itemKey="P1" initialNote="Baseline" onSaved={onSaved} />);
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Keep this draft' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save', exact: true }));
  await waitFor(() => expect(screen.getByText('Save failed: Device storage quota exceeded', { exact: true })).toBeTruthy());
  expect(screen.getByRole('textbox').value).toBe('Keep this draft');
  expect(onSaved).not.toHaveBeenCalled();
});
