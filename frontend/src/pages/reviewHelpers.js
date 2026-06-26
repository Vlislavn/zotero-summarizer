// Pure helpers extracted from Review.jsx to keep the page file under
// the per-page LOC budget. No React here.

// The canonical reading-priority vocabulary (one code = one meaning, Law of
// Similarity): must=emerald/positive, should=sky, could=amber/caution,
// dont=rose — matching PriorityBadge (ui/Badge.jsx) and the score histogram.
// (must_read used to read amber here, contradicting emerald everywhere else.)
export function priorityClass(priority) {
  switch (priority) {
    case 'must_read':
      return 'bg-emerald-100 text-emerald-800 border border-emerald-300';
    case 'should_read':
      return 'bg-sky-100 text-sky-800 border border-sky-300';
    case 'could_read':
      return 'bg-amber-100 text-amber-800 border border-amber-300';
    case 'dont_read':
      return 'bg-rose-100 text-rose-800 border border-rose-300';
    default:
      return 'bg-slate-100 text-slate-700 border border-slate-300';
  }
}

export function reviewPaperUrl(item) {
  if (!item) return '';
  return item.link || item.url || (item.doi ? `https://doi.org/${item.doi}` : '');
}
