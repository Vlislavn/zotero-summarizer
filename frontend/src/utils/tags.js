// App-internal machine tags — verdict (`label:`), relevance/signal namespaces
// (`zs:`, `d:`/`e:`/`n:`/`y:`/`g:` …), and `/…`-path feed tags — are NOT tags the
// user assigns by hand, so they're hidden from every human-facing tag list (the
// per-paper tag autocomplete + removable chips, and the Library "Top Tags"
// browse filter). Genuine content tags ("Computer Science - …") carry no
// short-prefix-colon, so they pass through. The user can still type any literal
// tag (Postel's Law).
export const MACHINE_TAG_RE = /^([a-z]{1,3}:|label:|\/)/i;

export const isMachineTag = (tag) => MACHINE_TAG_RE.test(String(tag || ''));
