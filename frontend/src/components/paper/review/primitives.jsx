import { CHIP_TONE } from './tones.js';

// Flat, reading-grade building blocks for the paper-review surfaces. The whole
// point: separate content with WHITESPACE + a single hairline rule + a small
// label — never with another bordered/tinted box (Law of Common Region: a
// boundary must answer a user question; nested boxes answer none). One outer
// container owns the only real border; everything in here lives inside it.

// A labelled stretch of content. Siblings are separated by ONE hairline via the
// parent's `divide-y divide-slate-200/60` — Section just owns the vertical
// rhythm and the optional eyebrow label.
export function Section({ label, action, children, className = '' }) {
  return (
    <section className={`py-4 first:pt-0 last:pb-0 ${className}`}>
      {(label || action) && (
        <div className="mb-2 flex items-center justify-between gap-2">
          {label && <SectionLabel>{label}</SectionLabel>}
          {action}
        </div>
      )}
      {children}
    </section>
  );
}

export function SectionLabel({ children, level = 2, className = '' }) {
  const Heading = `h${level}`;
  return (
    <Heading className={`text-[11px] uppercase tracking-[0.08em] font-medium text-slate-500 ${className}`}>
      {children}
    </Heading>
  );
}

// The ONE disclosure pattern. Native <details> (keyboard + no JS) with a single
// rotating chevron and an eyebrow-styled summary — replaces the four bespoke
// <details> looks the review surfaces used to hand-roll (Jakob's Law: a
// disclosure looks like a disclosure everywhere).
export function Disclosure({ summary, children, defaultOpen = false, count = null, open, onToggle }) {
  const controlled = open !== undefined;
  return (
    <details
      className="group"
      {...(controlled ? { open } : { open: defaultOpen ? true : undefined })}
      onToggle={onToggle ? (e) => onToggle(e.currentTarget.open) : undefined}
    >
      <summary className="flex items-center gap-1.5 cursor-pointer select-none list-none [&::-webkit-details-marker]:hidden rounded text-[11px] uppercase tracking-[0.08em] font-medium text-slate-500 hover:text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-400 focus-visible:ring-offset-1">
        <svg
          viewBox="0 0 12 12"
          className="h-2.5 w-2.5 shrink-0 transition-transform duration-150 group-open:rotate-90"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <path d="M4 2l4 4-4 4" />
        </svg>
        <span>{summary}</span>
        {count != null && <span className="text-slate-300 normal-case tracking-normal">· {count}</span>}
      </summary>
      <div className="mt-2.5">{children}</div>
    </details>
  );
}

// The ONE collapsible page section (calm accordion row): serif title + a one-line
// summary while closed, chevron on the right. Native <details>, so keyboard and
// no-JS work and every closed section keeps its content in the DOM. Sections the
// reader needs by default pass `defaultOpen`; everything else stays one quiet line.
export function Fold({ id, label, summary, defaultOpen = false, children }) {
  return (
    <details id={id} open={defaultOpen || undefined} className="group scroll-mt-20 border-t border-slate-200/70 first:border-t-0">
      <summary className="grid min-h-11 cursor-pointer select-none list-none grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 rounded py-3 [&::-webkit-details-marker]:hidden focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-forest-400 focus-visible:ring-offset-1">
        <div className="min-w-0">
          <h2 className="font-display text-[19px] font-light leading-snug text-forest-800">{label}</h2>
          {summary && <p className="mt-0.5 truncate !text-[13px] !leading-snug text-slate-500 group-open:hidden">{summary}</p>}
        </div>
        <svg viewBox="0 0 12 12" className="h-3 w-3 shrink-0 text-slate-400 transition-transform duration-150 group-open:rotate-90" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M4 2l4 4-4 4" />
        </svg>
      </summary>
      <div className="pb-5 pt-1">{children}</div>
    </details>
  );
}

// A soft pill badge. `tone` is a key into CHIP_TONE; unknown tones fall back to
// slate so a bad value never throws.
export function Chip({ tone = 'slate', title, children, className = '' }) {
  return (
    <span
      title={title}
      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium border ${
        CHIP_TONE[tone] || CHIP_TONE.slate
      } ${className}`}
    >
      {children}
    </span>
  );
}

// A label : value reading row (the digest's natural shape). The label is a quiet
// eyebrow; the value reads at content scale (13px / relaxed). Collapses to a
// stacked layout on narrow panes so the value never gets a 6-character column.
export function KeyVal({ label, children, tone = 'default' }) {
  if (children == null || children === '') return null;
  const valueCls =
    tone === 'pos' ? 'text-emerald-700' : tone === 'neg' ? 'text-rose-700' : 'text-slate-700';
  return (
    <div className="grid grid-cols-1 sm:grid-cols-[8rem_minmax(0,1fr)] gap-x-3 gap-y-0.5 items-baseline">
      <dt className="text-[11px] uppercase tracking-[0.06em] font-medium text-slate-500 pt-0.5">
        {label}
      </dt>
      <dd className={`text-[13px] leading-relaxed ${valueCls}`}>{children}</dd>
    </div>
  );
}

// A bulleted reading list (read parts / implementation / key findings).
export function Bullets({ items }) {
  const list = (items || []).filter(Boolean);
  if (list.length === 0) return null;
  return (
    <ul className="list-disc pl-5 text-[13px] leading-relaxed text-slate-700 space-y-0.5 marker:text-slate-300">
      {list.map((x, i) => (
        <li key={i}>{x}</li>
      ))}
    </ul>
  );
}

// A single inline statistic (label over value), borderless — for the rigor /
// relevance spine. Several sit in a row separated by a hairline, not boxes.
export function Stat({ label, value, hint }) {
  return (
    <div title={hint} className="min-w-0">
      <div className="text-[11px] uppercase tracking-[0.06em] font-medium text-slate-500">{label}</div>
      <div className="mt-0.5 text-sm font-medium text-slate-800 truncate">{value}</div>
    </div>
  );
}
