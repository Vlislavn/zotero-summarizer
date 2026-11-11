// Feed metadata arrives raw: arXiv items carry the whole search query as a
// "venue", TOC entries carry publisher strings as authors, and some abstracts
// are a single TOC line with literal HTML. These helpers keep that junk out of
// the paper header/byline (Nielsen: match the user's world, not the feed's).

const JUNK_RE = /search_query=|arxiv\s*query|https?:\/\/|[?&](start|max_results|id_list)=|table of contents/i;

// Drop a "venue" string that is actually a raw query / URL / TOC title.
export function cleanVenue(venue) {
  const s = String(venue || '').trim();
  if (!s || s.length > 80 || JUNK_RE.test(s)) return '';
  return s;
}

// Keep only plausible person names: drop publisher/TOC strings masquerading as
// authors (long, URL/query-like, or multi-colon "Publisher: Journal: Section").
export function cleanAuthors(authors) {
  if (!Array.isArray(authors)) return [];
  return authors.filter((author) => {
    const name = String(author?.name || '').trim();
    if (!name || name.length > 60 || JUNK_RE.test(name)) return false;
    if ((name.match(/:/g) || []).length >= 2) return false;
    return true;
  });
}

// Feed names carry an internal ordering code ("D1 Agents — …", "W1 Preprint — …");
// the reader sees the name without it.
export function feedLabel(name) {
  return String(name || '').replace(/^[A-Z]\d{1,2}\s+(?=\S)/, '');
}

// Publisher feeds prefix the abstract with "Nature, Published online: 13 August 2026; doi:10.1038/…".
const PUB_NOTICE_PREFIX = /^[^\n.;:]{0,80}?,?\s*published online[:;][^;]*;\s*doi:\s*\S+\s*/i;

// Strip literal HTML (TOC lines ship a bare `<br />`) and the journal/DOI notice so the abstract reads as text.
export function cleanAbstract(abstract) {
  const text = String(abstract || '')
    .replace(/<br\s*\/?>/gi, '\n')
    // Block ends ("…-5</a></p>An agentic…") carry the only separator; keep it.
    .replace(/<\/(p|div|li|h[1-6])>/gi, ' ')
    .replace(/<[^>]+>/g, '')
    .replace(/[ \t]{2,}/g, ' ')
    .trim();
  return text.replace(PUB_NOTICE_PREFIX, '') || text;
}
