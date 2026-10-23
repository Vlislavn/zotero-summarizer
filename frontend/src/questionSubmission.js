const UUID = /^[\da-f]{8}-[\da-f]{4}-[\da-f]{4}-[\da-f]{4}-[\da-f]{12}$/;
const QUESTION_FIELDS = ['submission_id', 'item_key', 'question', 'origin', 'preset_id'];

export function questionSubmissionError(row) {
  if (!row || typeof row !== 'object' || typeof row.submission_id !== 'string'
      || !UUID.test(row.submission_id)) return 'Invalid submission UUID';
  if (typeof row.item_key !== 'string' || !row.item_key.trim()
      || ['.', '..'].includes(row.item_key) || /[/\\\0]/.test(row.item_key)) return 'Invalid paper key';
  if (typeof row.question !== 'string' || !row.question.trim() || [...row.question].length > 2000) {
    return 'Question must be nonblank text of at most 2000 characters';
  }
  if (!['typed', 'preset'].includes(row.origin)) return 'Invalid question origin';
  if ((row.origin === 'preset' || row.preset_id != null)
      && (typeof row.preset_id !== 'string' || !row.preset_id.trim())) return 'Invalid preset ID';
  return null;
}

export function sameQuestion(left, right) {
  return QUESTION_FIELDS.every((field) => (left[field] ?? null) === (right[field] ?? null));
}

export function mergeQuestions(server = [], local = []) {
  const rows = new Map(server.map((row) => [row.submission_id, row]));
  for (const row of local) {
    const remote = rows.get(row.submission_id);
    rows.set(row.submission_id, remote && sameQuestion(remote, row) ? remote : row);
  }
  return [...rows.values()];
}
