import { CHIP_TONE as TONE_CLS } from '../paper/review/tones.js';

// Badge primitives — the small coloured pills the app sprinkles everywhere.
//
// Three flavours, all sharing the same color vocabulary as the rest of the UI
// (emerald=positive/must_read, sky=should_read, amber=warn/could_read,
// rose=error/dont_read, teal=brand, slate=neutral, violet=active):
//
//   StatusPill    — the operational ✓ / ✗ pill (re-homed from
//                   LlmRoutingSection so it lives with its siblings; that
//                   module re-exports it for back-compat with its importers).
//   PriorityBadge — must_read / should_read / could_read / dont_read in the
//                   canonical emerald / sky / amber / rose vocab.
//   ActionBadge   — a generic status/change pill: pass a `tone` token (or a
//                   raw className) + children.

// Tone → class map: re-uses the canonical CHIP_TONE vocabulary (tones.js, imported
// above) so a dont_read / B-grade / error reads the SAME shade in a Badge as in a
// Chip — one tone vocabulary app-wide (Law of Similarity), not a drifting copy.

// Operational check pill — exact markup the LlmRoutingSection original used so
// the Settings/Setup surfaces render byte-identically.
export function StatusPill({ status }) {
  const ok = status === 'operational';
  return (
    <span
      className={`inline-flex items-center gap-1 text-xs font-semibold px-2 py-0.5 rounded-full border ${
        ok
          ? 'bg-emerald-50 border-emerald-200 text-emerald-800'
          : 'bg-rose-50 border-rose-200 text-rose-800'
      }`}
    >
      <span aria-hidden>{ok ? '✓' : '✗'}</span>
      {ok ? 'operational' : 'fail'}
    </span>
  );
}

// Canonical reading-priority pill. The four priorities map to the standard
// emerald / sky / amber / rose vocab; an unknown value falls back to slate.
const PRIORITY_TONE = {
  must_read: 'emerald',
  should_read: 'sky',
  could_read: 'amber',
  dont_read: 'rose',
};

export function PriorityBadge({ priority, className = '' }) {
  const tone = PRIORITY_TONE[priority] || 'slate';
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded-full border text-xs font-semibold ${
        TONE_CLS[tone]
      }${className ? ` ${className}` : ''}`}
    >
      {priority || '?'}
    </span>
  );
}

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
