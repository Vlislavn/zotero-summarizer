import { useEffect, useRef, useState } from 'react';

export function noteDeliveryText(receipt) {
  const unknown = 'Zotero note delivery: unknown.';
  if (!receipt || typeof receipt.note_written !== 'boolean' || typeof receipt.note_pending !== 'boolean'
      || !Number.isSafeInteger(receipt.projection_revision) || receipt.projection_revision < 0
      || (receipt.note_error !== null && typeof receipt.note_error !== 'string')
      || (receipt.target_key !== null && typeof receipt.target_key !== 'string')) return unknown;
  const revision = ` (revision ${receipt.projection_revision})`;
  if (receipt.note_pending) {
    return `Zotero note delivery: pending${revision}${receipt.note_error ? ` — ${receipt.note_error}` : ''}.`;
  }
  if (receipt.note_error) return `Zotero note delivery: not confirmed${revision} — ${receipt.note_error}.`;
  if (receipt.projection_revision > 0 && receipt.target_key?.trim()) {
    return `Zotero note delivery: acknowledged${revision}.`;
  }
  return unknown;
}

function binding(itemKey, note, questions) {
  const rows = questions.map((row) => [row.submission_id, row.item_key ?? itemKey,
    row.question, row.origin ?? null, row.preset_id ?? null]);
  rows.sort((a, b) => a[0].localeCompare(b[0]));
  return JSON.stringify([itemKey, note, rows]);
}

export default function useNoteDelivery(itemKey, note, questions) {
  const identity = binding(itemKey, note, questions);
  const current = useRef(identity);
  const active = useRef(null);
  if (current.current !== identity) {
    current.current = identity;
    if (active.current?.expected !== identity) active.current = null;
  }
  const [report, setReport] = useState(null);
  useEffect(() => {
    setReport((previous) => previous?.request === active.current && previous.identity === identity ? previous : null);
  }, [identity]);

  function beginNoteDelivery({ question, note: savedNote = note } = {}) {
    if (current.current !== identity) return undefined;
    const projected = question && !questions.some((row) => row.submission_id === question.submission_id)
      ? [...questions, question] : questions;
    const request = { expected: binding(itemKey, savedNote, projected) };
    active.current = request;
    setReport(null);
    return (receipt) => {
      if (active.current !== request) return;
      if (!receipt) { active.current = null; setReport(null); return; }
      setReport({ request, identity: request.expected, receipt });
    };
  }

  return {
    questionMirror: report?.request === active.current && report.identity === identity ? report.receipt : null,
    beginNoteDelivery,
  };
}
