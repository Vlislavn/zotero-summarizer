import { useEffect, useRef, useState } from 'react';
import { fetchPaperRender, buildPaperRender } from '../../api/libraryApi.js';
import Spinner from '../ui/Spinner.jsx';

// One-click access to the full review from a Read-next row. The paper-read render
// artifact (notes/figures/HTML next to the Zotero PDF) used to be reached only
// via a 4-level drill-down; this button collapses that to a single click: ensure
// the artifact is built (build-on-demand + spinner), then open the review route
// (/paper/:key) in a new tab — which carries the verdict/collection/tag controls
// the static HTML brief never had. Reuses the existing render helpers — no
// backend change. A sibling of the row's expand <button> (never nested inside it
// — nested interactive elements are invalid).

const POLL_MS = 1500;
const MAX_POLLS = 240;  // ponytail: ~6 min cap; raise if real builds exceed it.
const NO_PDF_MESSAGE = 'No local PDF attached. Try Open Access and your configured Chrome/university session, then open the review.';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function briefErrorMessage(error) {
  const code = error?.body?.error || error?.body?.detail?.error || '';
  const message = error?.message || String(error || '');
  if (code === 'needs_pdf' || /no local pdf|no pdf/i.test(message)) {
    return 'No local PDF attached, and the full-text acquisition path could not fetch one.';
  }
  if (code === 'needs_library_login' || /needs_library_login|browser\/university|university.*session/i.test(message)) {
    return 'Chrome/university access could not fetch the publisher PDF. Open the paper in Chrome or refresh University access, then retry.';
  }
  if (code === 'not_found' && /presentation|generated/i.test(message)) {
    return 'The generated paper artifact is missing. Rebuild it, then open the review again.';
  }
  return message || 'Could not open the review.';
}

export default function OpenBriefButton({ itemKey, hasPdf = true, label = null }) {
  const [working, setWorking] = useState(false);
  const [error, setError] = useState(null);
  // Drop the in-flight result if the row unmounts mid-build (don't navigate a
  // tab / setState after teardown).
  const aliveRef = useRef(true);
  useEffect(() => () => { aliveRef.current = false; }, []);

  async function handleClick() {
    if (working) return;
    setError(null);
    // Open the tab synchronously, BEFORE any await, so the popup-blocker treats
    // it as user-initiated. Don't pass the 'noopener' feature — it makes open()
    // return null and we'd lose the handle; null `opener` afterwards instead.
    // A holding page explains the wait so a slow build isn't a blank, hung tab.
    const tab = window.open('about:blank', '_blank');
    if (tab) {
      tab.opener = null;
      tab.document.write(
        '<!doctype html><meta charset="utf-8"><title>Opening review…</title>'
        + '<body style="font:16px/1.5 system-ui;color:#334155;margin:0;display:flex;'
        + 'align-items:center;justify-content:center;height:100vh;text-align:center">'
        + '<p style="max-width:32em;padding:0 1.5em">Preparing the paper review — building '
        + 'the readable artifact first if needed. This can take a few minutes…</p>',
      );
    }
    setWorking(true);
    try {
      let render;
      try {
        render = await fetchPaperRender(itemKey);
      } catch (e) {
        const code = e?.body?.error || e?.body?.detail?.error || '';
        if (code !== 'needs_pdf') throw e;
        render = { status: 'missing', needs_pdf: true };
      }
      // Only kick a build when there's nothing usable; an already-running build
      // just needs polling (single-flight backend — don't double-trigger).
      if (render?.status !== 'completed' && render?.status !== 'running') {
        await buildPaperRender(itemKey, { allowAcquireMissing: !hasPdf });
      }
      if (render?.status !== 'completed') {
        for (let i = 0; i < MAX_POLLS; i += 1) {
          await sleep(POLL_MS);
          if (!aliveRef.current) { tab?.close(); return; }
          render = await fetchPaperRender(itemKey);
          if (render?.status === 'completed') break;
          if (render?.status === 'error') {
            const err = new Error(render.message || render.error || 'build failed');
            err.body = render;
            throw err;
          }
        }
      }
      if (render?.status !== 'completed') throw new Error('timed out building the paper artifact');
      // Open the INTERACTIVE full review (React route) — not the static HTML
      // artifact — so the new tab carries the verdict / collection / tag controls.
      // The build above still runs so the route's reader pane has a built brief.
      const url = `/paper/${encodeURIComponent(itemKey)}`;
      if (tab) tab.location.href = url;
      else window.open(url, '_blank', 'noopener');  // popup blocked → best-effort
    } catch (e) {
      tab?.close();
      if (aliveRef.current) setError(briefErrorMessage(e));
    } finally {
      if (aliveRef.current) setWorking(false);
    }
  }

  // Prominent labeled variant (the compact decision card's primary CTA) — same
  // build-on-demand + new-tab handler, rendered as a full-width labeled button so
  // "Open the full review" is the obvious next step (Fitts/Von Restorff). The
  // small icon variant (default, no label) stays the row-level shortcut.
  if (label) {
    return (
      <div className="space-y-1">
        <button
          type="button"
          onClick={handleClick}
          disabled={working}
          aria-busy={working}
          aria-describedby={error ? `${itemKey}-brief-error` : undefined}
          title={hasPdf ? 'Open the full review (builds it first if needed)' : NO_PDF_MESSAGE}
          className="w-full inline-flex items-center justify-center gap-2 px-3.5 py-2 rounded-lg bg-teal-700 text-white text-[13px] font-semibold hover:bg-teal-800 disabled:opacity-60"
        >
          {working && <Spinner size="sm" color="teal-on-fill" />}
          {working ? 'Opening…' : label}
        </button>
        {error && (
          <span
            id={`${itemKey}-brief-error`}
            role="status"
            className="block rounded-md border border-amber-200 bg-amber-50 px-2 py-1.5 text-[11px] leading-snug text-amber-900"
          >
            {error}
          </span>
        )}
      </div>
    );
  }

  return (
    <span className="relative shrink-0 inline-flex flex-col items-end">
      <button
        type="button"
        onClick={handleClick}
        disabled={working}
        aria-label={hasPdf ? 'Open full review' : 'Fetch and open full review'}
        aria-busy={working}
        aria-describedby={error ? `${itemKey}-brief-error` : undefined}
        title={
          error
            ? `Couldn't open the review: ${error}`
            : hasPdf
              ? 'Open the full review (builds the paper artifact first if needed)'
              : NO_PDF_MESSAGE
        }
        className={`shrink-0 flex items-center justify-center w-7 h-7 rounded text-sm hover:bg-teal-50 disabled:opacity-60 ${
          error
            ? 'text-rose-500 hover:text-rose-600'
            : hasPdf
              ? 'text-slate-400 hover:text-teal-700'
              : 'text-slate-300 hover:text-amber-700 hover:bg-amber-50'
        }`}
      >
        {working ? <Spinner size="sm" color="teal" /> : <span aria-hidden="true">{hasPdf ? 'ℹ' : 'PDF'}</span>}
      </button>
      {error && (
        <span
          id={`${itemKey}-brief-error`}
          role="status"
          className="absolute right-0 top-full z-20 mt-1 w-56 rounded-md border border-amber-200 bg-amber-50 px-2 py-1.5 text-left text-[11px] leading-snug text-amber-900"
        >
          {error}
        </span>
      )}
    </span>
  );
}
