import { afterEach, expect, it, vi } from 'vitest';
vi.mock('./settingsApi.js', () => ({ request: vi.fn() }));
import { request } from './settingsApi.js';
import { updatePaths } from './setupApi.js';
afterEach(() => vi.resetAllMocks());
it.each([{}, { written: ['ZOTERO_DATA_DIR'] }, { restart_required: 'true', written: ['ZOTERO_DATA_DIR'] },
  { restart_required: true, written: [] }, { restart_required: true, written: ['PDF_ROOT'] },
  { restart_required: true, written: ['ZOTERO_DATA_DIR', 'ZOTERO_DATA_DIR'] }])(
  'rejects unconfirmed/mismatched path-write receipt %j without retry', async acknowledgement => {
    request.mockResolvedValue(acknowledgement);
    await expect(updatePaths({ zotero_data_dir: '/synthetic/replacement' })).rejects.toThrow('Path save not confirmed');
    expect(request).toHaveBeenCalledTimes(1);
  });
it.each([null, [], { unsupported: '/synthetic' }, { constructor: '/synthetic' }])('rejects invalid path input %j before sending a request', async body => {
  await expect(updatePaths(body)).rejects.toThrow(); expect(request).not.toHaveBeenCalled();
});
it('accepts the exact keys actually acknowledged by the existing server contract', async () => {
  const response = { restart_required: true, written: ['PDF_ROOT', 'ZOTERO_DATA_DIR'], validated: { pdf_root_exists: true, zotero_db_found: true } };
  request.mockResolvedValue(response); const body = { zotero_data_dir: '/synthetic/zotero', pdf_root: '/synthetic/pdf' };
  expect(await updatePaths(body)).toEqual(response);
  expect(request).toHaveBeenCalledWith('/api/setup/paths', { method: 'PUT', body: JSON.stringify(body) });
});
