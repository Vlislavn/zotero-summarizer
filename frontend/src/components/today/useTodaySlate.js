import { useCallback, useMemo, useRef } from 'react';
import { memberPresentation } from '../../utils/todayVisit.js';
import { sortedFeedNames } from './todaySlate.js';

// The retained visit as cards: presentation per member, the decided ones filtered out, and the
// focus-keeping decide handler for the list.
export default function useTodaySlate(view, flow) {
  const allMembers = useMemo(() => (view.visit?.members || []).map(member => ({
    ...member, display: memberPresentation(member, view.fresh, view.staleReason),
  })), [view.visit, view.fresh, view.staleReason]);
  // Decided cards leave the slate (#206): they stay in the retained visit (identity, carry, the
  // «wait for Zotero» banner) but only undecided cards, and decided ones with a problem, render.
  const members = useMemo(() => view.checking ? [] : allMembers.filter(member =>
    !member.display.leaves && !flow.left.has(member.paper.item_id)), [allMembers, flow.left, view.checking]);
  const listRef = useRef(null);
  const decideCard = useCallback((paper, priority) => {
    // The focused button goes away with its card: keep keyboard and screen-reader position in the list.
    if (flow.decide(paper, priority)) requestAnimationFrame(() => listRef.current?.focus({ preventScroll: true }));
  }, [flow]);
  const feedNames = useMemo(() => sortedFeedNames(members), [members]);
  const hasVisit = view.visit && !view.visit.invalid;
  const caughtUp = hasVisit && !view.checking && allMembers.length > 0 && members.length === 0;
  return { allMembers, members, listRef, decideCard, feedNames, hasVisit, caughtUp };
}
