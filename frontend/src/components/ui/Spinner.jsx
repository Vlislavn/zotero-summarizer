// One spinner for the whole app — a Tailwind-only animated ring (no icon dep,
// keeps the bundle small). Replaces the ad-hoc `inline-block … animate-spin`
// spans that were copy-pasted across Today / PaperReaderPane / AskPaperBox /
// DeepReviewSection / ReadNextView / AdminSection.
//
// Behaviour-preserving by design: the `size` + `color` props map to the EXACT
// class strings the inline spinners used, so swapping a span for <Spinner/>
// renders pixel-identical output. The defaults match the most common variant
// (h-3.5 w-3.5, slate ring with a teal head). `aria-hidden` because the spinner
// is decorative — every call site pairs it with visible "Loading…" text or its
// own role="status" wrapper.

// size token -> the inline `h-_ w-_` pair the original spans used.
const SIZE_CLS = {
  xs: 'h-3 w-3',
  sm: 'h-3.5 w-3.5',
  md: 'h-4 w-4',
};

// color token -> the `border-*` ring + `border-t-*` head pair. Each key is one
// of the real combinations that existed in the codebase (Postel's Law: the
// vocabulary is exactly the set already in use, nothing invented).
const COLOR_CLS = {
  teal: 'border-slate-300 border-t-teal-600',
  slate: 'border-slate-300 border-t-slate-600',
  'slate-dark': 'border-slate-300 border-t-slate-700',
  'teal-on-fill': 'border-teal-200 border-t-white',
  'indigo-on-fill': 'border-indigo-200 border-t-white',
};

export default function Spinner({
  size = 'sm',
  color = 'teal',
  className = '',
}) {
  const sizeCls = SIZE_CLS[size] || SIZE_CLS.sm;
  const colorCls = COLOR_CLS[color] || COLOR_CLS.teal;
  return (
    <span
      aria-hidden="true"
      className={`inline-block rounded-full border-2 animate-spin ${sizeCls} ${colorCls}${
        className ? ` ${className}` : ''
      }`}
    />
  );
}
