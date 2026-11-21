import { Ungrounded } from './DigestRows.jsx';

// A field that could not be grounded is said so in its place, never shown as verified.
export const groundedField = (field, digest, lost, onRegenerate) => (
  lost.includes(field) ? <Ungrounded onRegenerate={onRegenerate} /> : digest?.[field] || null);
