// Presentation only: where a reported metric string ("Spearman ρ = 0.958") carries its
// number, and — only when the string is unambiguous — a compact visual. The verbatim
// string is always shown beside it, so a visual never says more than the text does.

// A number that stands on its own (not the 5 of "GPT-5" or the 50 of "IC50").
const NUMBER = String.raw`[+\-−]?\$?\d[\d,]*(?:\.\d+)?(?:\s?%|×)?`;
const VALUE = String.raw`(?<![A-Za-z\d.,/]-?)${NUMBER}`;
const LEAD = new RegExp(`${VALUE}(?:\\s?(?:to|vs\\.?|versus|–|—|/)\\s?${NUMBER})?`, 'g');
const VALUES = new RegExp(VALUE, 'g');
const NUMBERS = new RegExp(NUMBER, 'g');

const numberOf = (token) => Number(token.replace(/[^\d.+\-−]/g, '').replace('−', '-'));
const isPercent = (token) => token.includes('%');
const inPercentRange = (n) => n >= 0 && n <= 100;

// The visual for a lead expression that accounts for every number in the string.
function visualOf(lead) {
  const parts = lead.match(NUMBERS) || [];
  const [a, b] = parts.map(numberOf);
  if (parts.length === 1) {
    return isPercent(parts[0]) && !/^[+\-−]/.test(parts[0]) && inPercentRange(a) ? { kind: 'meter', fill: a / 100 } : null;
  }
  if (lead.includes('/')) return a >= 0 && b > 0 && a <= b ? { kind: 'meter', fill: a / b } : null;
  const sameUnit = isPercent(parts[0]) === isPercent(parts[1]);
  if (!sameUnit) return null;
  if (/vs|versus/.test(lead)) {
    const top = Math.max(Math.abs(a), Math.abs(b));
    return top > 0 && Math.sign(a) === Math.sign(b) ? { kind: 'pair', fills: [Math.abs(a) / top, Math.abs(b) / top] } : null;
  }
  return isPercent(parts[0]) && inPercentRange(a) && inPercentRange(b) && a < b
    ? { kind: 'range', from: a / 100, to: b / 100 } : null;
}

/** "accuracy (74.7% vs 74.0%)" → { before, lead: "74.7% vs 74.0%", after, visual }. `lead` is '' when no number stands out. */
export function metricView(text) {
  const raw = String(text || '').trim();
  const leads = [...raw.matchAll(LEAD)];
  // A measured value (percent, decimal, or after "=") outranks a bare count such as "≥90:".
  const pick = leads.find((m) => /[%.]/.test(m[0]) || /=\s*$/.test(raw.slice(0, m.index))) || leads[0];
  if (!pick) return { before: raw, lead: '', after: '', visual: null };
  const lead = pick[0];
  const before = raw.slice(0, pick.index);
  const after = raw.slice(pick.index + lead.length);
  const onlyNumber = !`${before} ${after}`.match(VALUES);
  return { before, lead, after, visual: onlyNumber ? visualOf(lead) : null };
}
