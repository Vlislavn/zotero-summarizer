// Below the reviewed cards: the best-ranked pool papers with no review yet. Each row is one
// tap target into the paper page, where the full review starts; no verdict or Add controls here.
import { Link } from 'react-router-dom';
import { trackToday } from '../../utils/todayEvents.js';
import { feedLabel } from '../../utils/feedText.js';

const MAX_WHY = 2;

export default function NotReviewedShelf({ papers, visitId }) {
  if (!papers?.length) return null;
  return (
    <section aria-labelledby="not-reviewed-title" className="mt-6">
      <h2 id="not-reviewed-title" className="text-sm font-medium text-slate-800">Not reviewed yet ({papers.length})</h2>
      <p className="mt-0.5 text-xs text-slate-500">Highest-ranked papers without a review. Open one to start its full review.</p>
      <ul className="mt-2 divide-y divide-slate-200 rounded-xl border border-slate-200">
        {papers.map(paper => (
          <li key={paper.item_id}>
            <Link to={`/paper/${encodeURIComponent(paper.stable_feed_key)}`}
              onClick={() => trackToday(visitId, { item_key: paper.stable_feed_key, item_id: paper.item_id, kind: 'open_review' })}
              className="block min-h-11 px-3 py-2 hover:bg-slate-50 focus-visible:bg-slate-50">
              <span className="block break-words text-[14px] leading-snug text-forest-800">{paper.title}</span>
              <span className="mt-0.5 block text-[12px] text-slate-500">
                {[feedLabel(paper.feed_name), paper.why?.slice(0, MAX_WHY).join(' · ')].filter(Boolean).join(' · ')}
              </span>
              {paper.bullets?.length > 0 && <span className="mt-1 block space-y-0.5">
                {paper.bullets.map(({ label, text }) => (
                  <span key={label} className="flex gap-2 text-[12px] leading-snug text-slate-700">
                    <span className="w-[3.25rem] shrink-0 font-medium text-forest-800">{label}</span>
                    <span className="min-w-0">{text}</span>
                  </span>
                ))}
              </span>}
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}
