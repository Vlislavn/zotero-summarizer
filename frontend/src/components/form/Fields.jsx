// Shared form primitives for the Settings page and the first-run wizard.
//
// Extracted verbatim out of Settings.jsx so both surfaces render byte-identical
// controls. `Field` gains an optional `error` prop (renders rose text + a rose
// border) so the wizard can surface inline field errors from validate-config.

import { useId } from 'react';

const INPUT_CLS =
  'w-full mt-1 p-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-teal-500';
const INPUT_CLS_ERR =
  'w-full mt-1 p-2 border border-rose-400 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-rose-500';

export function SectionCard({ title, description, children }) {
  return (
    <div className="glass rounded-2xl border border-slate-200 p-4">
      <h3 className="text-sm font-semibold uppercase tracking-wider text-slate-600">
        {title}
      </h3>
      {description && (
        <p className="text-xs text-slate-600 mt-1 mb-3">{description}</p>
      )}
      <div className={description ? '' : 'mt-3'}>{children}</div>
    </div>
  );
}

// One generic field component covers text / number / textarea / select. Cuts
// the per-input boilerplate vs. shipping five near-identical wrappers.
export function Field({
  label,
  kind = 'text',
  value,
  onChange,
  hint,
  error,
  options,
  rows = 8,
  step = 0.01,
  min,
  max,
  placeholder,
  disabled = false,
}) {
  const base = error ? INPUT_CLS_ERR : INPUT_CLS;
  const labelId = useId();
  const fieldAccessibility = {
    'aria-labelledby': labelId,
    'aria-describedby': [error && `${labelId}-error`, hint && `${labelId}-hint`].filter(Boolean).join(' ') || undefined,
    'aria-invalid': error ? true : undefined,
  };
  let control;
  if (kind === 'textarea') {
    control = (
      <textarea
        {...fieldAccessibility}
        disabled={disabled}
        value={value ?? ''}
        onChange={(e) => onChange(e.target.value)}
        rows={rows}
        placeholder={placeholder}
        className={`${base} font-mono`}
      />
    );
  } else if (kind === 'select') {
    control = (
      <select
        {...fieldAccessibility}
        disabled={disabled}
        value={value ?? ''}
        onChange={(e) => onChange(e.target.value)}
        className={`${base} bg-white`}
      >
        {(options || []).map((opt) => (
          <option key={typeof opt === 'object' ? opt.value : opt} value={typeof opt === 'object' ? opt.value : opt}>
            {typeof opt === 'object' ? opt.label : opt}
          </option>
        ))}
      </select>
    );
  } else if (kind === 'number') {
    control = (
      <input
        {...fieldAccessibility}
        type="number"
        disabled={disabled}
        value={value ?? 0}
        step={step}
        min={min}
        max={max}
        onChange={(e) => onChange(e.target.value === '' ? 0 : Number(e.target.value))}
        className={base}
      />
    );
  } else {
    control = (
      <input
        {...fieldAccessibility}
        type="text"
        disabled={disabled}
        value={value ?? ''}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className={base}
      />
    );
  }
  return (
    <label className="block">
      <span id={labelId} className="text-sm font-semibold text-slate-700">{label}</span>
      {control}
      {error && <span id={`${labelId}-error`} className="text-xs text-rose-600 mt-1 block">{error}</span>}
      {hint && <span id={`${labelId}-hint`} className="text-xs text-slate-600 mt-1 block">{hint}</span>}
    </label>
  );
}

export function CheckboxField({ label, checked, onChange, hint, disabled = false }) {
  return (
    <label className={`flex items-start gap-2 ${disabled ? 'cursor-not-allowed opacity-60' : 'cursor-pointer'}`}>
      <input
        type="checkbox"
        disabled={disabled}
        checked={Boolean(checked)}
        onChange={(e) => onChange(e.target.checked)}
        className="mt-1 rounded"
      />
      <span>
        <span className="text-sm font-semibold text-slate-700">{label}</span>
        {hint && (
          <span className="text-xs text-slate-600 mt-0.5 block">{hint}</span>
        )}
      </span>
    </label>
  );
}

export function Banner({ kind, children }) {
  if (!children) return null;
  const cls =
    kind === 'error'
      ? 'bg-rose-50 border-rose-200 text-rose-800'
      : 'bg-emerald-50 border-emerald-200 text-emerald-900';
  return (
    <div
      role="status"
      aria-live="polite"
      className={`px-3 py-2 rounded-lg border text-sm ${cls}`}
    >
      {children}
    </div>
  );
}
