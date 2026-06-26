// Wizard step 1 — Connect Zotero. Auto-detects the Zotero data dir; clicking a
// detected location SAVES it immediately (no second button, nothing to remember).
// Manual entry lives behind an "Enter path manually" disclosure for the rare
// no-candidates case. The live status row shows "DB found ✓ / N feeds" from the
// setup-status payload. Saving reports restart_required (carried to the Done step).

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { detectZotero, updatePaths } from '../../api/setupApi.js';
import { humanizeError } from '../../utils/humanizeError.js';
import { Banner, Field } from '../form/Fields.jsx';
import Button from '../ui/Button.jsx';

export default function StepConnectZotero({ status, draftPaths, onPatchPaths, onStatusChanged, onPathsSaved }) {
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
      queryClient.invalidateQueries({ queryKey: ['setup-status'] });
      onStatusChanged?.();
      onPathsSaved?.();
    },
  });

  const zotero = status?.zotero || {};
  const dbFound = Boolean(zotero.db_found);

  // Selecting a detected location applies AND saves it in one click.
  function applyCandidate(c) {
    onPatchPaths({ zotero_data_dir: c.data_dir });
    saveMutation.mutate({ zotero_data_dir: c.data_dir });
  }

  function handleSaveManual() {
    setRestartBanner('');
    if (!draftPaths.zotero_data_dir) return;
    saveMutation.mutate({ zotero_data_dir: draftPaths.zotero_data_dir });
  }

  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-base font-semibold text-slate-900">Connect Zotero</h3>
        <p className="text-sm text-slate-500 mt-1">
          We look for your Zotero data directory automatically. Click the right one
          to connect it, or enter the path manually.
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

      {/* Detected candidates — clicking one saves the path immediately. */}
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
              </div>
            </button>
          );
        })}
      </div>

      {/* Manual override — only needed when auto-detect missed; collapsed once a
          location was found. The PDF-root override moved to Settings → Advanced. */}
      <details open={candidates.length === 0} className="text-sm">
        <summary className="cursor-pointer select-none text-slate-500 hover:text-slate-800 w-fit">
          Enter path manually
        </summary>
        <div className="mt-2 space-y-3">
          <Field
            label="Zotero data directory"
            value={draftPaths.zotero_data_dir || ''}
            onChange={(v) => onPatchPaths({ zotero_data_dir: v })}
            placeholder="/Users/you/Zotero"
            hint="Folder containing zotero.sqlite and storage/."
          />
          <Button
            variant="secondary"
            onClick={handleSaveManual}
            disabled={saveMutation.isPending || !draftPaths.zotero_data_dir}
          >
            {saveMutation.isPending ? 'Saving…' : 'Save path'}
          </Button>
        </div>
      </details>

      {saveMutation.error && (
        <Banner kind="error">{humanizeError(saveMutation.error)}</Banner>
      )}
      {restartBanner && <Banner kind="success">{restartBanner}</Banner>}
    </div>
  );
}
