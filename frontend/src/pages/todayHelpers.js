export function fulltextMessage(fulltext) {
  if (!Array.isArray(fulltext?.outcomes)) return null;
  const unavailable = fulltext.outcomes.filter(
    (row) => !String(row.status || '').startsWith('attached_') && row.status !== 'skipped_has_pdf',
  ).length;
  return { text: `PDFs attached ${fulltext.attached || 0}${unavailable ? `; ${unavailable} full text unavailable` : ''}`, unavailable };
}
