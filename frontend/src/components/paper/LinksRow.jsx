import { itemPdfUrl } from '../../api/libraryApi.js';

// Links to the abstract / DOI / full PDF — the expected affordance for "open the
// paper" (Jakob's/Occam's). Shared by the Library inline panel and the Annotate
// detail. `detail` is the /api/golden/review-detail payload.
export default function LinksRow({ detail, itemKey }) {
  const url = detail.url;
  const doi = detail.doi;
  const hasPdf = detail.has_pdf;
  if (!url && !doi && !hasPdf) {
    return <div className="text-[11px] text-slate-400 italic">No link or PDF on file.</div>;
  }
  const linkCls = 'px-2 py-0.5 rounded-lg border border-slate-300 text-slate-700 text-xs hover:bg-slate-50';
  return (
    <div className="flex flex-wrap items-center gap-2">
      {url && (
        <a href={url} target="_blank" rel="noreferrer" className={linkCls}>Abstract ↗</a>
      )}
      {doi && (
        <a href={`https://doi.org/${doi}`} target="_blank" rel="noreferrer" className={linkCls}>DOI ↗</a>
      )}
      {hasPdf && (
        <a href={itemPdfUrl(itemKey)} target="_blank" rel="noreferrer"
          className="px-2 py-0.5 rounded-lg bg-emerald-600 text-white text-xs font-semibold hover:bg-emerald-700">
          PDF ↗
        </a>
      )}
    </div>
  );
}
