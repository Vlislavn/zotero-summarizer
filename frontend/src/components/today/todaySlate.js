// Pure derivations for the Today slate: who is visible, selectable and ticked.
export function pickMembers(members, feedFilter, selectedIds) {
  const visible = members.filter(member => !feedFilter || member.paper.feed_name === feedFilter);
  const selectable = visible.filter(member => member.display.selectable);
  const selected = selectable.filter(member => selectedIds.has(member.paper.item_id));
  const selectedReady = selected.filter(member => member.display.reviewReady);
  const allSelected = selectable.length > 0 && selectable.every(member => selectedIds.has(member.paper.item_id));
  return { visible, selectable, selected, selectedReady, allSelected };
}

export function sortedFeedNames(members) {
  return [...new Set(members.map(member => member.paper.feed_name).filter(Boolean))].sort();
}
