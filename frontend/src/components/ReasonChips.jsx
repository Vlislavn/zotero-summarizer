import { NEG_CHIPS, chipClass } from './decisionVocab.js';

// Optional «why» chips under an active «Not a fit». `value`: null = not rejected, [] = no reason, [...] = chips.
export default function ReasonChips({ value, onChange, disabled = false }) {
  if (value == null) return null;
  const toggle = (id) => onChange(value.includes(id) ? value.filter((chip) => chip !== id) : [...value, id]);
  return <div>
    <p className="text-xs text-slate-600">Why not a fit?</p>
    <div className="mt-1 flex flex-wrap gap-1.5">
      {NEG_CHIPS.map(([id, label]) => <button key={id} type="button" onClick={() => toggle(id)}
        aria-pressed={value.includes(id)} disabled={disabled} className={chipClass(value.includes(id))}>{label}</button>)}
    </div>
  </div>;
}
