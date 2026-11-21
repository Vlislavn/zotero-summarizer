import { groupRows } from './readNextCopy.js';
import { BALANCED_ORDER } from '../../utils/readNextOrder.js';
import { DEFAULT_SORT } from '../../utils/relevanceBands.js';

// Constants and pure helpers of the Read-next list (ReadNextView and its hooks).
export const REVEAL_STEP = 60;  // rows revealed initially and per "Show more" click
const MUST_PREVIEW = 3;  // your own Must read rows shown before "Show all" (2 on a phone: the first undecided paper stays near the top)
const MUST_PREVIEW_PHONE = 2;
export const mustPreviewFor = (phone) => (phone ? MUST_PREVIEW_PHONE : MUST_PREVIEW);

// Default order without a search: three groups, computed from each row's own verdict
// (so the offline working set groups too). Any other sort, or a search, is one flat
// list where a decided row says so itself. The reveal pages through the undecided rows.
export function listLayout({ items, sort, semantic, searchQuery, visibleCount }) {
  const groups = sort.field === 'best' && !semantic && !searchQuery.trim() ? groupRows(items) : null;
  const shown = (groups ? groups.todo : items).slice(0, visibleCount);
  const pagedTotal = groups ? groups.todo.length : items.length;
  return { groups, shown, pagedTotal };
}

// The rows currently in the list, in screen order.
export const rowNodes = (listRef) => [...(listRef.current?.querySelectorAll('[data-row-key]') || [])];

// The verdict a row already carries, in the shape the verdict flow takes as `prior`.
export function priorOf(queueItems, key) {
  const row = queueItems.find((it) => it.item_key === key);
  return row?.user_priority ? { user_priority: row.user_priority, comment: '' } : null;
}

const NO_OPEN = { key: null, open() {}, close() {}, position: null, total: 0, prev: null, next: null };
const DEFAULTS = {
  // Bulk "Add to collection" (the Meaning-search → Zotero collection shortcut):
  // flat collection list + handler from Library; last target remembered so the
  // common "send to my reading collection" case is one click (Working Memory).
  collections: [], addingToCollection: false, serverAvailable: true, offline: false,
  // Client-side smart filters (Library owns the state; `items` arrives already
  // filtered). rawCount = pre-filter size, so we can tell "nothing fetched" apart
  // from "filtered to zero". filterSig = serialized filters, to reset the reveal.
  rawCount: 0, hasActiveFilters: false, filterSig: '',
  // Hybrid "Meaning" search: `semantic` = the list is ranked by similarity to
  // `searchQuery`; `rerankerLoading` = the cross-encoder is still downloading (we
  // show fusion order meanwhile); `semanticUnavailable` = corpus off (text match).
  semantic: false, searchQuery: '', rerankerLoading: false, semanticUnavailable: false,
  // Explicit Zotero-column sort (Library owns the state; `items` arrive already
  // sorted). 'best' = the blended server order.
  sort: DEFAULT_SORT,
  // Read-next order presets: `orderMeta` ({coverage, fill, balancedWeights}) is set only
  // where the presets can act (no search, parts in the response); Library owns `order` and,
  // while no Zotero field is chosen, has already sorted `items` by it.
  order: BALANCED_ORDER, orderMeta: null,
  // True while the list is the whole library (no collection / tag scope): only then is
  // «Goals strong» (top third of goal_sim in the loaded list) the same word for a paper everywhere.
  cohortFull: false,
  // The open paper: {key, open(key), close(), position, total, prev, next} from useOpenPaper.
  openState: NO_OPEN,
  // A verdict patches its row in place (the page owns the rows): onRowsChange([[key, fields]…]), onWrite(promise).
  onRowsChange: () => {}, onWrite: () => {},
  // `/` puts the cursor in the page's search box.
  onFocusSearch: null,
};

// The props of ReadNextView with their defaults: like a destructuring default, only `undefined` falls back
// (`queueItems` defaults to `items`).
export function withDefaults(props) {
  const given = Object.fromEntries(Object.entries(props).filter(([, value]) => value !== undefined));
  return { queueItems: props.items, ...DEFAULTS, ...given };
}
