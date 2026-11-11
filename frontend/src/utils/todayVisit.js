import { readStorage, writeStorage } from './safeStorage.js';

export const TODAY_VISIT_QUERY = ['today-visit'];
export const TODAY_MEMBERS_QUERY = ['today-members'];
export const TODAY_STORAGE_KEY = 'zs.todayVisit.v1';
export const VISIT_LIMIT = 15;

function validPaper(paper) {
  return paper && Number.isSafeInteger(paper.item_id) && paper.item_id > 0
    && typeof paper.item_key === 'string' && paper.item_key.length > 0;
}

export function readTodayVisit() {
  const raw = readStorage(TODAY_STORAGE_KEY);
  if (raw === null) return null;
  try {
    const visit = JSON.parse(raw);
    if (visit?.version === 1 && typeof visit.id === 'string' && visit.id
      && Array.isArray(visit.members) && visit.members.length <= VISIT_LIMIT
      && visit.members.every(member => validPaper(member?.paper))
      && new Set(visit.members.map(member => member.paper.item_id)).size === visit.members.length) return visit;
  } catch { /* Preserve invalid initialization as an error, not an automatic new selection. */ }
  return { id: 'unreadable', invalid: true, members: [] };
}

// The same owner publishes Today and shared-reader feedback. A stale selection
// response cannot replace another tab's newer shortlist; carried request IDs can
// still receive their own acknowledgement, without replacing any other member.
export function updateTodayVisit(client, transform, expectedId) {
  const cached = client.getQueryData(TODAY_VISIT_QUERY);
  const stored = readTodayVisit();
  const current = stored && stored.id !== cached?.id ? stored : cached;
  if (expectedId !== undefined && (current?.id || null) !== expectedId) {
    client.setQueryData(TODAY_VISIT_QUERY, current);
    return { updated: false };
  }
  if (!current && expectedId === undefined) return { updated: false };
  const next = transform(current);
  if (!next) return { updated: false };
  const serialized = JSON.stringify(next);
  const saved = writeStorage(TODAY_STORAGE_KEY, serialized) && readStorage(TODAY_STORAGE_KEY) === serialized;
  if (!saved && current && next.id !== current.id) return { updated: false, saved: false };
  client.setQueryData(TODAY_VISIT_QUERY, next);
  return { updated: true, saved };
}

export function startTodayReviewAction(client, visitId, itemKey, priority) {
  if (!visitId) throw new Error('Return to Today before submitting a decision from this link.');
  if (client.isMutating({ mutationKey: ['today-selection'] })) throw new Error('Wait for the new shortlist before submitting a decision.');
  const action = { id: crypto.randomUUID(), kind: 'review', priority, phase: 'unconfirmed' };
  let found = false;
  const result = updateTodayVisit(client, current => ({ ...current, members: current.members.map(member => {
    if (member.paper.stable_feed_key !== itemKey) return member;
    found = true;
    return { ...member, action };
  }) }), visitId);
  if (!result.updated || !found) throw new Error('This Today shortlist changed. Return to Today before submitting a decision.');
  return action.id;
}

export function finishTodayReviewAction(client, actionId, response, error) {
  if (!actionId) return;
  let refused = false;
  try { refused = ['review_required', 'validation_error', 'not_found'].includes(JSON.parse(error?.body)?.error); } catch { /* Unknown effects. */ }
  const saved = [];
  updateTodayVisit(client, current => ({ ...current, members: current.members.map(member => {
    if (member.action?.id !== actionId) return member;
    const acknowledged = member.action.priority === null
      ? ['number', 'boolean'].includes(typeof response?.deleted) : Number.isInteger(response?.id);
    const problem = error?.message || response?.add_error || response?.label_error || response?.note_error || response?.reject_tag_error || null;
    const replied = acknowledged && !response.saved_offline;
    // A saved decision shows on the card at once (as when deciding on Today); the members
    // refetch then replaces these facts with the server's.
    const facts = replied && !refused && !problem && member.facts ? { ...member.facts, user_priority: member.action.priority } : member.facts;
    if (facts !== member.facts) saved.push([member.paper.item_id, member.action.priority]);
    return { ...member, facts, action: { ...member.action,
      phase: refused ? 'refused' : replied ? 'replied' : 'unconfirmed', error: problem,
    } };
  }) }));
  // The cached members reply (kept for revalidate-on-return) must not re-apply the old facts.
  client.setQueriesData({ queryKey: TODAY_MEMBERS_QUERY }, data => (data?.members ? { ...data, members: data.members.map(row => {
    const hit = saved.find(([id]) => id === row.item_id);
    return hit ? { ...row, user_priority: hit[1] } : row;
  }) } : data));
}

export function matchesMember(paper, facts) {
  return facts?.available === true && facts.item_id === paper.item_id
    && facts.item_key === paper.item_key
    && (facts.stable_feed_key || '') === (paper.stable_feed_key || '');
}

export function memberNeedsCarry(member) {
  const facts = member.facts;
  const action = member.action;
  if (facts?.zotero_sync_status === 'pending') return true;
  if (facts?.note_delivery?.pending && facts.note_delivery.target_key) return true;
  if (action?.failure?.code === 'review_required' || action?.phase === 'refused') return false;
  if (!action) return false;
  if (!matchesMember(member.paper, facts)) return true;
  if (action.kind === 'review') {
    return action.phase !== 'replied' || Boolean(action.error)
      || (facts.user_priority || null) !== action.priority;
  }
  if (facts.user_priority === 'dont_read' || facts.zotero_sync_status === 'cancelled') {
    // A terminal Trash reply confirms that best-effort read marking has finished,
    // not necessarily succeeded. A lost reply requires actual read-mark evidence.
    return action.kind === 'trash' && action.phase !== 'replied' && !facts.read_time_marked_at;
  }
  if (action.kind === 'add' && facts.materialized_zotero_key) {
    return facts.note_delivery?.pending !== false;
  }
  return true;
}

export function createTodayVisit(slate, previous) {
  if (!Array.isArray(slate?.papers) || slate.papers.length > VISIT_LIMIT || !slate.papers.every(validPaper)) {
    throw new Error('The selection response is invalid; the previous shortlist was kept.');
  }
  const id = slate.slate_id || crypto.randomUUID(); // the logged slate id joins exposure events
  const members = previous?.members.filter(memberNeedsCarry) || [];
  const seenIds = new Set(members.map(member => member.paper.item_id));
  const seenKeys = new Set(members.flatMap(member => [member.paper.item_key, member.paper.stable_feed_key]).filter(Boolean));
  for (const paper of slate.papers) {
    if (members.length === VISIT_LIMIT) break;
    if (seenIds.has(paper.item_id) || seenKeys.has(paper.item_key)
      || (paper.stable_feed_key && seenKeys.has(paper.stable_feed_key))) continue;
    members.push({ paper, selectionPolicy: slate.selection_policy, selectionId: id, facts: null, action: null });
    seenIds.add(paper.item_id);
    seenKeys.add(paper.item_key);
    if (paper.stable_feed_key) seenKeys.add(paper.stable_feed_key);
  }
  return { version: 1, id, members, preparation: slate.preparation ?? null, unreviewed: slate.unreviewed ?? [] };
}

// Preparation status captured with the shortlist; visits saved before it show none.
export function preparationText(preparation) {
  if (!preparation || !(preparation.left > 0)) return null;
  return `At selection: ${preparation.ready} analyzed papers ready, ${preparation.left} ${
    preparation.running ? 'still being analyzed' : 'not analyzed yet'}.`;
}

const NOT_RECOMMENDED = {
  no_current_review: 'its review is no longer current',
  source_unavailable: 'full text is unavailable',
  quality_not_assessed: 'quality was not assessed',
  quality_excluded: 'quality was flagged',
  dont_read: 'not worth reading',
};

// Selection-time recommendation, with a current-facts downgrade for analyzed picks.
export function recommendationView(paper, current) {
  const selected = paper.recommendation;
  if (!selected?.eligible) return null;
  if (current && current.eligible === false) {
    return { ...selected, withdrawn: `No longer recommended: ${NOT_RECOMMENDED[current.reason] || current.reason}.` };
  }
  return { ...selected, ...(current?.eligible ? current : {}), withdrawn: null };
}

export function applyMemberFacts(visit, response) {
  if (!Array.isArray(response?.members) || response.members.length !== visit.members.length) {
    throw new Error('Member status is incomplete. No papers were replaced.');
  }
  const facts = new Map(response.members.map(member => [member.item_id, member]));
  if (facts.size !== visit.members.length || visit.members.some(member => !facts.has(member.paper.item_id))) {
    throw new Error('Member status returned different identities. No papers were replaced.');
  }
  return { ...visit, members: visit.members.map(member => {
    const current = facts.get(member.paper.item_id);
    return { ...member, facts: matchesMember(member.paper, current) ? current : {
      item_id: member.paper.item_id, available: false,
      reason: current.available === false ? current.reason : 'identity_changed',
    } };
  }) };
}

export function actionReply(action, response, id) {
  const count = response?.[action.kind === 'add' ? 'added' : 'trashed'];
  if (!Number.isInteger(count) || count < 0 || count > action.ids.length || !Array.isArray(response?.failed)
    || !response.failed.every(item => Number.isSafeInteger(item?.id) && action.ids.includes(item.id))
    || (response.note_mirrors !== undefined && (!Array.isArray(response.note_mirrors)
      || !response.note_mirrors.every(item => Number.isSafeInteger(item?.id) && action.ids.includes(item.id))))
    || !Number.isInteger(response.failed_count) || response.failed_count !== response.failed.length) {
    return { ...action, phase: 'unconfirmed', error: 'Action reply was incomplete; refresh status before retrying.' };
  }
  return { ...action, phase: 'replied', failure: response.failed.find(item => item.id === id) || null,
    note: response.note_mirrors?.find(item => item.id === id) || null,
    readError: response.marked_read_error || null,
    fulltext: response.fulltext || null };
}

export function memberPresentation(member, fresh) {
  const facts = member.facts;
  const action = member.action;
  const available = matchesMember(member.paper, facts);
  const rejected = facts?.user_priority === 'dont_read' || facts?.decision === 'user_rejected';
  const materialized = Boolean(facts?.materialized_zotero_key);
  const pending = facts?.zotero_sync_status === 'pending';
  const unresolved = memberNeedsCarry(member);
  // No boilerplate before a decision (#37/T08): the status line appears only once
  // the owner has acted or a side-effect is pending.
  let text = '';
  if (rejected) text = 'Decision saved: don’t read.';
  else if (materialized) text = 'Zotero item recorded.';
  else if (pending) text = 'Decision saved; Zotero delivery pending.';
  else if (facts?.user_priority) text = `Decision saved: ${facts.user_priority.replaceAll('_', ' ')}.`;
  if (facts?.note_delivery?.pending && facts.note_delivery.target_key) text += ' Note delivery pending.';
  if (action?.failure) text += ` ${action.failure.error || 'Action failed.'}`;
  if (action?.phase === 'unconfirmed') text += ' Previous action reply unconfirmed; current facts are shown separately.';
  if (action?.error) text += ` ${action.error}`;
  if (action?.readError) text += ` Feed read marking failed: ${action.readError}`;
  if (action?.note?.note_error && facts?.note_delivery?.pending !== false) text += ` Note: ${action.note.note_error}.`;
  const attachment = Array.isArray(action?.fulltext?.outcomes)
    ? action.fulltext.outcomes.find(outcome => outcome?.item_key === facts?.materialized_zotero_key) : null;
  if (attachment && !String(attachment.status || '').startsWith('attached_') && attachment.status !== 'skipped_has_pdf') {
    text += ` Optional full text unavailable: ${attachment.reason || attachment.status}.`;
  }
  if (action?.fulltext?.error) text += ` Batch reported optional full text unavailable: ${action.fulltext.error}`;
  if (!fresh) text = facts
    ? (text ? `Updating status. Last known: ${text}` : 'Updating status…')
    : 'Loading this paper…';
  else if (!available) text = 'This member is unavailable or its identity changed. No replacement was selected.';
  const handled = rejected || materialized || Boolean(facts?.user_priority)
    || ['selected', 'black_swan', 'user_approved', 'dedup_library', 'dedup_processed'].includes(facts?.decision);
  const canRetry = fresh && available && unresolved && ['add', 'trash'].includes(action?.kind)
    && (action.phase === 'replied' || materialized || (rejected && action.kind === 'trash'));
  return {
    text, unresolved, canRetry,
    selectable: fresh && available && !handled && !unresolved,
    // A review already seen stays ready while status refreshes (no flash after Add/verdict);
    // reviewKnown says whether "no review" is a fact, not a refresh in flight.
    reviewReady: available && facts.review_ready === true,
    reviewKnown: fresh && available,
    recommendation: fresh && available ? facts.recommendation ?? null : null,
  };
}

export function todayPaperUrl(key, visitId, { autoRun = false } = {}) {
  const query = new URLSearchParams({ from: 'today', visit: visitId });
  if (!autoRun) query.set('autoRun', 'false');
  return `/paper/${encodeURIComponent(key)}?${query}`;
}
