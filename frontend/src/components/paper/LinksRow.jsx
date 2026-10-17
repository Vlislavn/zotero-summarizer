import { itemPdfUrl } from '../../api/libraryApi.js';
import useServerAvailable from '../../hooks/useServerAvailable.js';

// Links to the abstract / DOI / full PDF — the expected affordance for "open the
// paper" (Jakob's/Occam's). Shared by the Library inline panel and the Annotate
// detail. `detail` is the /api/golden/review-detail payload.
export default function LinksRow({ detail, itemKey }) {
  const serverAvailable = useServerAvailable();
  const url = detail.url;
  const doi = detail.doi;
  const hasPdf = detail.has_pdf;
  if (!url && !doi && !hasPdf && !detail.offline_snapshot) {
    return <div className="text-[11px] text-slate-400 italic">No link or PDF on file.</div>;
  }
  const linkCls = 'px-2 py-0.5 rounded-lg border border-slate-300 text-slate-700 text-xs hover:bg-slate-50';
  return (
    <div className="flex flex-wrap items-center gap-2">
      {detail.offline_snapshot && <p role="status" className="basis-full text-xs text-amber-800">Compact synced context — summary and abstract may be abbreviated. The full review needs the Mac.</p>}
      {url && (
        <a href={url} target="_blank" rel="noreferrer" className={linkCls}>Abstract ↗</a>
      )}
      {doi && (
        <a href={`https://doi.org/${doi}`} target="_blank" rel="noreferrer" className={linkCls}>DOI ↗</a>
      )}
      {hasPdf && !serverAvailable && <span aria-disabled="true" className="text-xs text-slate-500">PDF unavailable — needs the Mac</span>}
      {hasPdf && serverAvailable && (
        <a href={itemPdfUrl(itemKey)} target="_blank" rel="noreferrer"
          className="px-2 py-0.5 rounded-lg bg-emerald-600 text-white text-xs font-semibold hover:bg-emerald-700">
          PDF ↗
        </a>
      )}
    </div>
  );
}
