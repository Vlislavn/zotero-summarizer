import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  buildPaperRender,
  fetchPaperRender,
  paperPresentationUrl,
  paperFigureUrl,
} from '../../api/libraryApi.js';
import Spinner from '../ui/Spinner.jsx';
import { Disclosure } from '../paper/review/primitives.jsx';

// Figures & full brief. A build job writes notes, figures and a single-file HTML
// brief next to the Zotero PDF. The decision content (verdict / quality / goals /
// digest) now renders natively in <PaperReview>, so this pane no longer iframes
// the whole document (which was a second design system embedded inside the app —
// "embedding in embedding"). Instead it shows the figures as native thumbnails
// and links out to the standalone/printable brief, and owns the build controls.
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
  const figures = (render?.figures || []).filter((f) => f && f.name);
  const version = render?.built_at || render?.pdf_key;

  // A stale artifact (built by an older renderer revision) rebuilds itself once
  // so the figures/brief reflect the current renderer.
  useEffect(() => {
    if (render?.stale && !running && !staleRebuiltRef.current) {
      staleRebuiltRef.current = true;
      buildMutation.mutate({ force: true });
    }
  }, [render?.stale, running]); // eslint-disable-line react-hooks/exhaustive-deps

  const summary = (
    <>
      Figures &amp; full brief
      {render && (
        <span className="ml-1.5 normal-case tracking-normal text-slate-300">
          · {render.status}
          {completed ? ` · ${render.figures_count || 0} fig · ${render.source_tier}` : ''}
        </span>
      )}
    </>
  );

  return (
    <Disclosure summary={summary} open={Boolean(open)} onToggle={onOpenChange}>
      <div className="space-y-3 text-[13px] leading-relaxed text-slate-700">
        {renderQuery.isLoading && <div className="text-slate-500">Checking generated paper brief…</div>}
        {renderQuery.error && (
          <div className="text-rose-700">Render status failed: {renderQuery.error.message || String(renderQuery.error)}</div>
        )}
        {render?.status === 'missing' && (
          <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-amber-800">
            No paper brief yet. Build will write notes, the HTML brief, figures and audit files next to the PDF.
          </div>
        )}
        {render?.status === 'error' && (
          <div className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-rose-800">
            Build failed: {render.error || render.message}
          </div>
        )}
        {render?.stale && completed && (
          <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-amber-800">
            This brief was built by an older renderer — rebuilding with the latest…
          </div>
        )}
        {running && (
          <div className="flex items-center gap-2 text-slate-500" role="status" aria-live="polite">
            <Spinner size="sm" color="slate" />
            Building notes, figures, the HTML brief and audit…
          </div>
        )}

        {completed && figures.length > 0 && (
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-2.5">
            {figures.map((fig, i) => (
              <a
                key={fig.name}
                href={paperFigureUrl(itemKey, fig.name)}
                target="_blank"
                rel="noreferrer"
                className="group block overflow-hidden rounded-md border border-slate-200 bg-white hover:border-teal-300"
                title={fig.caption || fig.label || `Figure ${i + 1}`}
              >
                <img
                  src={paperFigureUrl(itemKey, fig.name)}
                  alt={fig.caption || `Figure ${i + 1}`}
                  loading="lazy"
                  className="w-full h-28 object-contain bg-slate-50"
                />
                <div className="px-2 py-1 text-[11px] text-slate-500 line-clamp-2 group-hover:text-slate-700">
                  {fig.caption || fig.label || `Figure ${i + 1}`}
                </div>
              </a>
            ))}
          </div>
        )}

        {completed && (
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[12px] text-slate-500">
            <span>
              {render.sections_count || 0} sections · {render.figures_count || 0} figures · {render.references_count || 0} references
              {render.audit?.status ? ` · audit ${render.audit.status}` : ''}
              {render.audit?.blocking?.length ? ` (${render.audit.blocking.length} blocking)` : ''}
            </span>
            <a
              href={paperPresentationUrl(itemKey, version)}
              target="_blank"
              rel="noreferrer"
              className="font-semibold text-teal-700 hover:text-teal-800"
            >
              Open full brief ↗
            </a>
          </div>
        )}

        <label className="flex items-center gap-2 text-[12px] text-slate-500">
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
            className="rounded-lg bg-teal-700 px-3.5 py-2 text-[13px] text-white font-semibold hover:bg-teal-800 disabled:opacity-50"
          >
            {completed ? 'Refresh if changed' : 'Build paper brief'}
          </button>
          {completed && (
            <button
              type="button"
              disabled={running}
              onClick={() => buildMutation.mutate({ force: true })}
              className="rounded-lg border border-slate-300 px-3.5 py-2 text-[13px] font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-50"
            >
              Rebuild
            </button>
          )}
        </div>
        {completed && (
          <p className="text-[11px] text-slate-400">
            Ran a deep review since this was built? Rebuild to bake the digest into the standalone brief.
          </p>
        )}
        {buildMutation.error && (
          <div className="text-rose-700">Build start failed: {buildMutation.error.message || String(buildMutation.error)}</div>
        )}
      </div>
    </Disclosure>
  );
}
