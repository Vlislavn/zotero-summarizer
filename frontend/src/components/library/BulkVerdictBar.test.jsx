// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import BulkVerdictBar from './BulkVerdictBar.jsx';
import * as api from '../../api/libraryApi.js';
import { decide } from '../../utils/verdictUndo.js';

vi.mock('../../api/libraryApi.js');
vi.mock('../../utils/verdictUndo.js', () => ({ decide: vi.fn() }));

const onDone = vi.fn();
const bar = () => render(<BulkVerdictBar keys={['A', 'B']} onDone={onDone} onVerdicts={vi.fn()} priorOf={() => null} />);
const click = async (name) => act(async () => { fireEvent.click(screen.getByRole('button', { name })); });

beforeEach(() => { vi.resetAllMocks(); decide.mockReturnValue({ done: Promise.resolve() }); });
afterEach(cleanup);

it('Not interested asks in a sheet with the count and the backup line; Cancel writes nothing', async () => {
  const confirm = vi.spyOn(window, 'confirm');
  bar();
  await click('Set Not interested for 2 selected');
  const sheet = screen.getByRole('dialog');
  expect(within(sheet).getByRole('heading', { name: 'Mark 2 papers Not interested?' })).toBeTruthy();
  expect(sheet.textContent).toMatch(/Backup first/);
  await click('Cancel');
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(decide).not.toHaveBeenCalled();
  expect(confirm).not.toHaveBeenCalled();
});

it('Mark read asks first with the count and the backup line; Cancel writes nothing', async () => {
  bar();
  await click('Mark read (2)');
  const sheet = screen.getByRole('dialog');
  expect(within(sheet).getByRole('heading', { name: 'Mark 2 papers read?' })).toBeTruthy();
  expect(sheet.textContent).toMatch(/Backup first/);
  expect(api.updateItemTags).not.toHaveBeenCalled();
  await click('Cancel');
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(api.updateItemTags).not.toHaveBeenCalled();
});

it('Mark read: a running Zotero opens the «Zotero is running…» sheet; Cancel stops with N of M', async () => {
  api.updateItemTags.mockResolvedValue({ requires_force: true });
  bar();
  await click('Mark read (2)');
  await click('Mark read');
  expect(within(screen.getByRole('dialog')).getByRole('heading', { name: 'Zotero is running…' })).toBeTruthy();
  await click('Cancel');
  expect(api.updateItemTags).toHaveBeenCalledTimes(1);
  expect(onDone).toHaveBeenCalledWith('Marked read stopped after 0 of 2: cancelled', true, false);
});

it('Mark read: Write anyway repeats the tag change with force and stops at the first failure', async () => {
  api.updateItemTags.mockResolvedValueOnce({ requires_force: true }).mockResolvedValueOnce({ ok: true }).mockRejectedValue(new Error('disk'));
  bar();
  await click('Mark read (2)');
  await click('Mark read');
  await click('Write anyway');
  expect(api.updateItemTags.mock.calls[1][1]).toMatchObject({ force: true });
  expect(onDone).toHaveBeenCalledWith('Marked read stopped after 1 of 2: disk', true, true);
});
