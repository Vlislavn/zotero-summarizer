// Wizard step 1 — Connect Zotero. Auto-detects the Zotero data dir, lets the
// user confirm or override the path + the PDF root, then saves via PUT
// /api/setup/paths (which reports restart_required). The live status row shows
// "DB found ✓ / N feeds ✓" straight from the setup-status payload; Next is
// gated on status.zotero.db_found after a Re-detect.

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { detectZotero, updatePaths } from '../../api/setupApi.js';
import { humanizeError } from '../../utils/humanizeError.js';
import { Banner, Field } from '../form/Fields.jsx';

export default function StepConnectZotero({ status, draftPaths, onPatchPaths, onStatusChanged }) {
  const queryClient = useQueryClient();
  const [restartBanner, setRestartBanner] = useState('');

  const detectQuery = useQuery({
    queryKey: ['setup-detect-zotero'],
    queryFn: detectZotero,
    staleTime: 30_000,
  });
  const candidates = detectQuery.data?.candidates || [];

  const saveMutation = useMutation({
    mutationFn: updatePaths,
    onSuccess: () => {
      setRestartBanner('Restart required to apply new paths.');
      // The backend re-reads paths only on restart, but invalidating refreshes
      // the existence/feed-count flags it can compute live.
      queryClient.invalidateQueries({ queryKey: ['setup-status'] });
      onStatusChanged?.();
    },
  });

  const zotero = status?.zotero || {};
  const dbFound = Boolean(zotero.db_found);

  function applyCandidate(c) {
    onPatchPaths({ zotero_data_dir: c.data_dir });
  }

  function handleSave() {
    setRestartBanner('');
    const body = {};
    if (draftPaths.zotero_data_dir) body.zotero_data_dir = draftPaths.zotero_data_dir;
    if (draftPaths.pdf_root) body.pdf_root = draftPaths.pdf_root;
    saveMutation.mutate(body);
  }

  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-base font-semibold text-slate-900">Connect Zotero</h3>
        <p className="text-sm text-slate-500 mt-1">
          We look for your Zotero data directory automatically. Confirm it below
          or point us at a different location.
        </p>
      </div>

      {/* Live status row from the setup-status payload. */}
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <span
          className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full border font-semibold ${
            dbFound
              ? 'bg-emerald-50 border-emerald-200 text-emerald-800'
              : 'bg-rose-50 border-rose-200 text-rose-800'
          }`}
        >
          <span aria-hidden>{dbFound ? '✓' : '✗'}</span>
          {dbFound ? 'Zotero DB found' : 'DB not found'}
        </span>
        {dbFound && (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full border border-emerald-200 bg-emerald-50 text-emerald-800 font-semibold">
            <span aria-hidden>✓</span>
            {zotero.feed_count ?? 0} feed{zotero.feed_count === 1 ? '' : 's'}
          </span>
        )}
        {dbFound && typeof zotero.library_item_count === 'number' && (
          <span className="text-xs text-slate-500">
            {zotero.library_item_count} library item
            {zotero.library_item_count === 1 ? '' : 's'}
          </span>
        )}
      </div>

      {zotero.error && <Banner kind="error">{zotero.error}</Banner>}

      {/* Detected candidates. */}
      <div className="space-y-2">
        <div className="flex items-center justify-between gap-2">
          <span className="text-sm font-semibold text-slate-700">Detected locations</span>
          <button
            type="button"
            onClick={() => detectQuery.refetch()}
            disabled={detectQuery.isFetching}
            className="text-xs text-teal-700 hover:text-teal-900 underline disabled:opacity-50"
          >
            {detectQuery.isFetching ? 'Detecting…' : 'Re-detect'}
          </button>
        </div>
        {detectQuery.isLoading && (
          <p className="text-xs text-slate-500">Scanning for Zotero…</p>
        )}
        {!detectQuery.isLoading && candidates.length === 0 && (
          <p className="text-xs text-slate-500 italic">
            No Zotero data dir auto-detected — enter the path manually below.
          </p>
        )}
        {candidates.map((c) => {
          const selected = draftPaths.zotero_data_dir === c.data_dir;
          return (
            <button
              type="button"
              key={c.data_dir}
              onClick={() => applyCandidate(c)}
              className={`w-full text-left rounded-xl border p-3 text-sm transition-colors ${
                selected
                  ? 'border-teal-400 bg-teal-50'
                  : 'border-slate-200 bg-white hover:bg-slate-50'
              }`}
            >
              <div className="font-mono text-xs text-slate-800 break-all">{c.data_dir}</div>
              <div className="mt-1 flex flex-wrap gap-2 text-[11px]">
                <span className={c.db_exists ? 'text-emerald-700' : 'text-rose-700'}>
                  {c.db_exists ? '✓ zotero.sqlite' : '✗ no zotero.sqlite'}
                </span>
                <span className={c.storage_exists ? 'text-emerald-700' : 'text-slate-400'}>
                  {c.storage_exists ? '✓ storage/' : 'no storage/'}
                </span>
                <span className="text-slate-400">source: {c.source}</span>
              </div>
            </button>
          );
        })}
      </div>

      {/* Manual override + PDF root. */}
      <div className="grid gap-4">
        <Field
          label="Zotero data directory"
          value={draftPaths.zotero_data_dir || ''}
          onChange={(v) => onPatchPaths({ zotero_data_dir: v })}
          placeholder="/Users/you/Zotero"
          hint="Folder containing zotero.sqlite and storage/."
        />
        <Field
          label="PDF root (optional)"
          value={draftPaths.pdf_root || ''}
          onChange={(v) => onPatchPaths({ pdf_root: v })}
          placeholder="Defaults to the Zotero storage/ folder"
          hint="Where generated paper-read artifacts are written next to the PDF."
        />
      </div>

      <div className="flex items-center gap-3 flex-wrap">
        <button
          type="button"
          onClick={handleSave}
          disabled={saveMutation.isPending || !draftPaths.zotero_data_dir}
          className="px-4 py-2 rounded-lg bg-slate-900 text-white text-sm font-semibold hover:bg-slate-700 disabled:bg-slate-300 disabled:cursor-not-allowed"
        >
          {saveMutation.isPending ? 'Saving…' : 'Save paths'}
        </button>
        <span className="text-xs text-slate-500">Then Re-detect to confirm the DB is found.</span>
      </div>

      {saveMutation.error && (
        <Banner kind="error">{humanizeError(saveMutation.error)}</Banner>
      )}
      {restartBanner && <Banner kind="success">{restartBanner}</Banner>}
    </div>
  );
}
