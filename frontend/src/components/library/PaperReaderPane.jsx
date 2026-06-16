import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  buildPaperRender,
  fetchPaperRender,
  paperPresentationUrl,
} from '../../api/libraryApi.js';
import Spinner from '../ui/Spinner.jsx';

// Paper brief: a build job writes notes, figures and a single-file HTML brief
// (hero, digest, readable sections, figures) next to the Zotero PDF. The pane
// embeds that brief inline so you can take in the paper at a glance without
// leaving the tab. A code-derived renderer revision marks stale artifacts so an
// out-of-date brief rebuilds itself.
export default function PaperReaderPane({ itemKey, open, onOpenChange }) {
  const queryClient = useQueryClient();
  const [allowArxivSource, setAllowArxivSource] = useState(false);
  const staleRebuiltRef = useRef(false);
  const renderQuery = useQuery({
    queryKey: ['paper-render', itemKey],
    queryFn: () => fetchPaperRender(itemKey),
    enabled: Boolean(open && itemKey),
    refetchInterval: (query) => (query.state.data?.status === 'running' ? 1500 : false),
  });
  const buildMutation = useMutation({
    mutationFn: ({ force = false } = {}) =>
      buildPaperRender(itemKey, { force, allowArxivSource }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['paper-render', itemKey] }),
  });
  const render = renderQuery.data;
  const completed = render?.status === 'completed';
  const running = render?.status === 'running' || buildMutation.isPending;

  // A stale artifact (built by an older renderer revision) rebuilds itself once
  // so the embedded brief reflects the current renderer.
  useEffect(() => {
    if (render?.stale && !running && !staleRebuiltRef.current) {
      staleRebuiltRef.current = true;
      buildMutation.mutate({ force: true });
    }
  }, [render?.stale, running]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <details
      open={open}
      onToggle={(e) => onOpenChange?.(e.target.open)}
      className="rounded-xl border border-slate-200 bg-white"
    >
      <summary className="cursor-pointer select-none px-3 py-2 text-[11px] uppercase tracking-wider font-semibold text-slate-500">
        Paper brief
        {render && (
          <span className="ml-2 normal-case font-normal text-slate-400">
            {render.status}
            {completed ? ` · ${render.source_tier} · ${render.n_pages || 0} pages` : ''}
          </span>
        )}
      </summary>
      <div className="px-3 pb-3 space-y-2 text-xs text-slate-700">
        {renderQuery.isLoading && <div className="text-slate-500">Checking generated paper brief…</div>}
        {renderQuery.error && (
          <div className="text-rose-700">Render status failed: {renderQuery.error.message || String(renderQuery.error)}</div>
        )}
        {render?.status === 'missing' && (
          <div className="rounded-lg border border-amber-200 bg-amber-50 p-2 text-amber-800">
            No paper brief yet. Build will write notes, the HTML brief, figures and audit files next to the PDF.
          </div>
        )}
        {render?.status === 'error' && (
          <div className="rounded-lg border border-rose-200 bg-rose-50 p-2 text-rose-800">
            Build failed: {render.error || render.message}
          </div>
        )}
        {render?.stale && completed && (
          <div className="rounded-lg border border-amber-200 bg-amber-50 p-2 text-amber-800">
            This brief was built by an older renderer — rebuilding with the latest…
          </div>
        )}
        {running && (
          <div className="flex items-center gap-2 text-slate-500" role="status" aria-live="polite">
            <Spinner size="sm" color="slate" />
            Building notes, figures, the HTML brief and audit…
          </div>
        )}
        {completed && (
          <div className="space-y-2">
            <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-2 text-emerald-900 space-y-1">
              <div>
                Generated {render.sections_count || 0} sections, {render.figures_count || 0} figures,
                {' '}{render.references_count || 0} references.
              </div>
              <div>
                Audit: {render.audit?.status || 'unknown'}
                {render.audit?.blocking?.length ? ` (${render.audit.blocking.length} blocking)` : ''}
              </div>
            </div>
            <iframe
              title="Paper brief"
              src={paperPresentationUrl(itemKey, render.built_at || render.pdf_key)}
              className="w-full h-[70vh] rounded-lg border border-slate-200 bg-white"
            />
            <p className="text-[10px] text-slate-400">
              Ran a deep review since this was built? Rebuild to bake the digest into the brief.
            </p>
          </div>
        )}
        <label className="flex items-center gap-2 text-[11px] text-slate-500">
          <input
            type="checkbox"
            checked={allowArxivSource}
            onChange={(e) => setAllowArxivSource(e.target.checked)}
            disabled={running}
          />
          Allow arXiv source download when detected
        </label>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            disabled={running}
            onClick={() => buildMutation.mutate({ force: false })}
            className="rounded-lg bg-teal-600 px-3 py-1.5 text-white font-semibold hover:bg-teal-700 disabled:opacity-50"
          >
            {completed ? 'Refresh if changed' : 'Build paper brief'}
          </button>
          {completed && (
            <button
              type="button"
              disabled={running}
              onClick={() => buildMutation.mutate({ force: true })}
              className="rounded-lg border border-slate-300 px-3 py-1.5 font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-50"
            >
              Rebuild
            </button>
          )}
        </div>
        {buildMutation.error && (
          <div className="text-rose-700">Build start failed: {buildMutation.error.message || String(buildMutation.error)}</div>
        )}
      </div>
    </details>
  );
}
