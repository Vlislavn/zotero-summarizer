import { useEffect, useState } from 'react';
import { formatWhen } from '../../utils/stepModelStatus.js';

// New shortlist outcome: compare member ids before/after on the client (the call itself is a re-rank).
export default function useTodayRenewal(view, allMembers) {
  const [renewal, setRenewal] = useState(null);
  const [renewResult, setRenewResult] = useState(null);
  const startRenewal = () => {
    setRenewResult(null);
    setRenewal({ visitId: view.visit?.id, ids: new Set(allMembers.map(member => member.paper.item_id)),
      at: view.visit?.createdAt });
    view.renew();
  };
  useEffect(() => {
    if (!renewal) return;
    if (view.selectionError) { setRenewal(null); return; }
    if (!view.visit || view.visit.invalid || view.visit.id === renewal.visitId) return;
    setRenewResult({ added: view.visit.members.filter(member => !renewal.ids.has(member.paper.item_id)).length,
      since: formatWhen(renewal.at) });
    setRenewal(null);
  }, [renewal, view.visit, view.selectionError]);
  return { renewResult, startRenewal };
}
