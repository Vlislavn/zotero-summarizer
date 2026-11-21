import { CHIP_TONE as TONE_CLS } from '../paper/review/tones.js';

// Badge primitive — the small coloured pill the app sprinkles everywhere. It shares the color
// vocabulary of the rest of the UI (emerald=positive/must_read, sky=should_read, amber=warn/could_read,
// rose=error/dont_read, teal=brand, slate=neutral, violet=active).

// Tone → class map: re-uses the canonical CHIP_TONE vocabulary (tones.js, imported
// above) so a dont_read / B-grade / error reads the SAME shade in a Badge as in a
// Chip — one tone vocabulary app-wide (Law of Similarity), not a drifting copy.

// Generic status / change pill. Provide either a `tone` token (mapped to the
// shared vocab) or a raw `className` for a bespoke palette; `className`, when
// given, fully replaces the tone classes so a call site can opt out of the
// standard look without fighting it.
export function ActionBadge({ tone = 'slate', className, children }) {
  const cls =
    className != null
      ? className
      : `border ${TONE_CLS[tone] || TONE_CLS.slate}`;
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-semibold ${cls}`}
    >
      {children}
    </span>
  );
}
