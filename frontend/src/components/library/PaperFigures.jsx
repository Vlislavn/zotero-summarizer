import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { buildPaperRender, fetchPaperRender, paperFigureUrl } from '../../api/libraryApi.js';
import Spinner from '../ui/Spinner.jsx';

const escapeRegExp = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
// Extracted captions repeat their own label ("Figure 1: …" under label "Figure 1").
const captionText = (fig) => {
  const caption = (fig.caption || '').trim();
  if (!fig.label) return caption;
  return caption.replace(new RegExp(`^${escapeRegExp(fig.label.trim())}\\s*[:.\\-–—]?\\s*`, 'i'), '');
};
const isTable = (fig) => /^table\b/i.test(fig.label || '');

// Always-visible figure strip for the full-page story view (the inline Library
// card keeps PaperReaderPane's disclosure + build controls — a different surface
// with a different presentation, so the ~15 lines of query/build duplication here
// are deliberate, not a shared-hook refactor of that 3-surface component).
//
// Read-mostly: it AUTO-BUILDS the render once if a local Zotero PDF exists
// (figures are cropped into a local artifact next to it — no Zotero data write).
// If a render artifact already exists for an acquired/cached PDF, it displays it
// too; it never starts full-text acquisition on page load.
// ponytail: duplicates PaperReaderPane's build/poll; unify only if a 3rd figure
// surface appears.
export default function PaperFigures({ itemKey, hasPdf = true, canBuild = hasPdf }) {
  const queryClient = useQueryClient();
  const [zoom, setZoom] = useState(null);
  const autoBuiltRef = useRef(null);
  const dialogRef = useRef(null);
  const renderQuery = useQuery({
    queryKey: ['paper-render', itemKey],
    queryFn: () => fetchPaperRender(itemKey),
    enabled: Boolean(itemKey),
    refetchInterval: (query) => (query.state.data?.status === 'running' ? 1500 : false),
  });
  const buildMutation = useMutation({
    mutationFn: ({ force = false } = {}) => buildPaperRender(itemKey, { force }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['paper-render', itemKey] }),
  });
  const render = renderQuery.data;
  const status = render?.status;
  const completed = status === 'completed';
  const running = status === 'running' || buildMutation.isPending;
  // A stale artifact's files are refused by the server (404 not_ready): never draw them.
  const stale = Boolean(render?.stale);
  const figures = stale ? [] : (render?.figures || []).filter((f) => f && f.name);
  const groups = [
    ['Figures', figures.filter((f) => !isTable(f))],
    ['Tables', figures.filter(isTable)],
  ].filter(([, items]) => items.length);

  // Build the brief ONCE per item when it's missing (or rebuild a stale artifact),
  // so the figures render without a manual click. Ref guard survives StrictMode.
  useEffect(() => {
    if (!itemKey || running || !canBuild) return;
    const needsBuild = status === 'missing' || render?.stale;
    if (!needsBuild) return;
    if (autoBuiltRef.current === itemKey) return;
    autoBuiltRef.current = itemKey;
    buildMutation.mutate({ force: Boolean(render?.stale) });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [itemKey, status, render?.stale, running, canBuild]);

  // Native <dialog> drives the lightbox: showModal() gives top-layer placement,
  // Esc + backdrop dismiss, a focus trap and an `inert` background for free — so
  // the old manual Escape listener + fixed-overlay/z-index scrim are gone (a Baseline
  // Widely-Available primitive, ~96.66% — no fallback needed). `onClose` syncs state.
  useEffect(() => {
    const d = dialogRef.current;
    if (!d) return;
    if (zoom && !d.open) d.showModal();
    else if (!zoom && d.open) d.close();
  }, [zoom]);

  if (renderQuery.error) {
    return <p className="text-[12px] text-rose-700">Figures unavailable: {renderQuery.error.message || String(renderQuery.error)}</p>;
  }
  if (!running && !stale && !figures.length && status !== 'completed') return null;

  return (
    <div className="space-y-2.5">
      {running && !figures.length && (
        <div className="flex items-center gap-2 text-[13px] text-slate-500" role="status" aria-live="polite">
          <Spinner size="sm" color="slate" /> {stale ? 'Rebuilding figures…' : 'Rendering figures…'}
        </div>
      )}
      {stale && !running && (
        <div className="flex flex-wrap items-center gap-2 text-[13px] text-slate-600" role="status">
          <span>Figures are outdated{buildMutation.isError ? ` — rebuild failed: ${buildMutation.error?.message || 'error'}` : ''}.</span>
          {canBuild && (
            <button
              type="button"
              onClick={() => buildMutation.mutate({ force: true })}
              className="min-h-11 rounded-md border border-slate-300 bg-white px-3 text-[13px] font-medium text-slate-700 hover:border-teal-400 focus:outline-none focus-visible:ring-2 focus-visible:ring-teal-400"
            >
              {buildMutation.isError ? 'Retry' : 'Rebuild'}
            </button>
          )}
        </div>
      )}
      {groups.map(([heading, items]) => (
        <section key={heading} aria-label={heading} className="space-y-1.5">
          {groups.length > 1 && <h3 className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">{heading}</h3>}
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-2.5">
            {items.map((fig) => {
              const caption = captionText(fig);
              const fallback = fig.label || heading.slice(0, -1);
              return (
                <button
                  key={fig.name}
                  type="button"
                  onClick={() => setZoom(fig)}
                  className="group block overflow-hidden rounded-lg border border-slate-200 bg-white text-left hover:border-teal-400 focus:outline-none focus-visible:ring-2 focus-visible:ring-teal-400 focus-visible:ring-offset-1"
                  title={fig.caption || fallback}
                >
                  <img
                    src={paperFigureUrl(itemKey, fig.name)}
                    alt={caption || fallback}
                    loading="lazy"
                    className="w-full h-32 object-contain bg-slate-50"
                  />
                  <div className="px-2 py-1 text-[11px] text-slate-500 line-clamp-2 group-hover:text-slate-700">
                    <span className="font-semibold text-slate-600">{fallback} </span>
                    {caption}
                  </div>
                </button>
              );
            })}
          </div>
        </section>
      ))}
      {completed && !stale && figures.length === 0 && (
        <p className="text-[12px] text-slate-400">No figures were detected in this paper.</p>
      )}
      <dialog
        ref={dialogRef}
        onClose={() => setZoom(null)}
        onClick={(e) => { if (e.target === dialogRef.current) dialogRef.current.close(); }}
        className="m-auto max-h-[85vh] max-w-3xl overflow-auto rounded-2xl border border-slate-200 bg-white p-0 backdrop:bg-slate-900/40 backdrop:backdrop-blur-sm"
      >
        {zoom && (
          <figure className="p-4" onClick={(e) => e.stopPropagation()}>
            <img
              src={paperFigureUrl(itemKey, zoom.name)}
              alt={captionText(zoom) || zoom.label || 'Figure'}
              className="mx-auto max-h-[70vh] w-auto"
            />
            {(zoom.caption || zoom.label) && (
              <figcaption className="mt-3 max-w-[66ch] text-[13px] leading-relaxed text-slate-600">
                {zoom.label && <span className="font-semibold text-slate-800">{zoom.label}. </span>}
                {captionText(zoom)}
              </figcaption>
            )}
          </figure>
        )}
      </dialog>
    </div>
  );
}
