// The one button primitive. Replaces the ~6 ad-hoc `px-* py-* rounded-lg bg-*`
// strings that had drifted (py-1.5 / py-2 / py-2.5; bg-slate-900 vs bg-indigo-600)
// so every button reads one size/variant vocabulary (Law of Similarity / Jakob's
// Law). Pass `variant` + `size`; everything else (onClick, disabled, type, title…)
// flows through. Not a framework — just the shared shape these buttons already wanted.

// Primary = the ONE saturated action color (Ease Health Forest Ink). It must be
// the same Forest the rest of the app already uses (the remapped teal/forest
// ramp) — never neutral near-black, which would make nav chrome the loudest mark.
const VARIANTS = {
  primary: 'bg-forest-800 text-white hover:bg-forest-700 disabled:bg-slate-300 disabled:text-slate-100',
  secondary: 'bg-white border border-slate-300 text-slate-700 hover:bg-slate-100 disabled:opacity-50',
  ghost: 'text-slate-600 hover:bg-slate-100 disabled:opacity-50',
  danger: 'bg-rose-600 text-white hover:bg-rose-700 disabled:bg-slate-300',
};
const SIZES = {
  sm: 'px-3 py-1.5 text-xs',
  md: 'px-4 py-2 text-sm',
};

export default function Button({
  variant = 'primary',
  size = 'md',
  type = 'button',
  className = '',
  children,
  ...rest
}) {
  return (
    <button
      type={type}
      className={`rounded-lg font-medium transition-colors disabled:cursor-not-allowed ${
        SIZES[size] || SIZES.md
      } ${VARIANTS[variant] || VARIANTS.primary} ${className}`}
      {...rest}
    >
      {children}
    </button>
  );
}
