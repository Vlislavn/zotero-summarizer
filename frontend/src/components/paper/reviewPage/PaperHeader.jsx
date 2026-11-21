import AuthorByline from '../../AuthorByline.jsx';
import LinksRow from '../LinksRow.jsx';
import { cleanAuthors, cleanVenue } from '../../../utils/feedText.js';

export default function PaperHeader({ detail, itemKey }) {
  const venueText = cleanVenue(detail.venue);
  return (
    <header className="mb-5 mt-2">
      <h1 className="max-w-[30ch] font-display text-[28px] font-light leading-[1.12] tracking-tight text-slate-900 sm:text-[32px]">
        {detail.title}
      </h1>
      <div className="mt-1.5"><AuthorByline authors={cleanAuthors(detail.authors)} quiet limit={3} /></div>
      {(venueText || detail.year) && (
        <div className="text-[12px] text-slate-500">
          <span className="text-slate-600">{venueText}</span>{venueText && detail.year ? ' · ' : ''}{detail.year}
        </div>
      )}
      <div className="mt-3"><LinksRow detail={detail} itemKey={itemKey} /></div>
    </header>
  );
}
