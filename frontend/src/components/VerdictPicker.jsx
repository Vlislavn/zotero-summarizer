import { PRIORITIES } from './VerdictPanel.jsx';

// One-click verdict chooser — the "fire-and-advance" sibling of VerdictPanel.
//
// VerdictPanel is the full editor (comment textarea, Previously/Edit/Delete,
// confirm-on-overwrite). Some surfaces instead need a single horizontal row of
// the four priorities that commits on one click and immediately advances
// (Feed Review's relabel row, the blind Re-label Audit). They share ONE human
// vocabulary and palette with the panel by importing the same `PRIORITIES`
// (Jakob's Law / Mental Model: "Must read … Remove ❌" reads the same
// everywhere; the raw `must_read` enum never reaches the user). Collapsing the
// four bespoke raw-enum buttons each surface used to hand-roll into this one
// control is the Hick/Occam win — same shape, same words, one source of truth.
//
// Props:
//   value: string | null   — currently-picked priority key (for the active ring)
//   onPick: (key) => void   — called with the wire value ('must_read' | …)
//   disabled: boolean       — whole row disabled (e.g. action already taken)
//   size: 'sm' | 'md'       — 'sm' for dense relabel rows, 'md' for the audit card
//   label: string | null    — optional inline prefix (e.g. "Relabel:")
export default function VerdictPicker({
  value = null,
  onPick = () => {},
  disabled = false,
  size = 'sm',
  label = null,
}) {
  const pad = size === 'md' ? 'px-3 py-1.5 text-sm' : 'px-2.5 py-1 text-xs';
  return (
    <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Set reading priority">
      {label && <span className="text-xs text-slate-500 mr-0.5">{label}</span>}
      {PRIORITIES.map((p) => {
        const active = value === p.key;
        return (
          <button
            key={p.key}
            type="button"
            disabled={disabled}
            aria-pressed={active}
            onClick={() => onPick(p.key)}
            className={`${pad} rounded-lg font-semibold transition-colors border focus:outline-none focus-visible:ring-2 focus-visible:ring-teal-500 disabled:opacity-50 disabled:cursor-not-allowed ${
              active
                ? `${p.cls} border-transparent`
                : 'bg-white border-slate-300 text-slate-700 hover:bg-slate-100'
            }`}
          >
            {p.label}
          </button>
        );
      })}
    </div>
  );
}
