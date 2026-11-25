import { useEffect, useId, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

// The one modal-surface primitive (full screen on phones, a 420 px right panel, a bottom confirmation).
// Mounted only while open, like RetrainConfirm. While mounted: focus moves to the title and returns to the
// opener on close, Esc closes, Tab stays inside, everything else on <body> is `inert` and does not scroll.
// Portal: glass ancestors (backdrop-filter) would otherwise clip a fixed overlay.
const FOCUSABLE = 'a[href],button:not([disabled]),input:not([disabled]):not([type=hidden]),select:not([disabled]),'
  + 'textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';
// Root: `inset-0` is the fallback height where `dvh` is unknown; `h-dvh` (the visible height under iOS toolbars) wins elsewhere.
const PANEL = {
  full: 'inset-0',
  side: 'inset-y-0 right-0 w-[420px] max-w-full border-l',
  bottom: 'inset-x-0 bottom-0 mx-auto w-full max-w-lg max-h-[85%] rounded-t-2xl border-t',
};

export default function Sheet({ title, variant = 'full', onClose, children }) {
  const titleId = useId();
  const root = useRef(null);
  const panel = useRef(null);
  const heading = useRef(null);
  const [opener] = useState(() => document.activeElement);

  useEffect(() => {
    const inerted = [...document.body.children].filter((el) => el !== root.current && !el.hasAttribute('inert'));
    inerted.forEach((el) => el.setAttribute('inert', ''));
    const overflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    heading.current?.focus({ preventScroll: true });
    return () => {
      inerted.forEach((el) => el.removeAttribute('inert'));
      document.body.style.overflow = overflow;
      if (opener?.isConnected && opener !== document.body) opener.focus({ preventScroll: true });
    };
  }, [opener]);

  useEffect(() => {
    const onKey = (event) => {
      if (event.key === 'Escape' && !event.defaultPrevented) { event.preventDefault(); onClose(); return; }
      if (event.key !== 'Tab') return;
      const items = [...panel.current.querySelectorAll(FOCUSABLE)];
      const first = items[0] || heading.current;
      const last = items[items.length - 1] || heading.current;
      const active = document.activeElement;
      const inside = panel.current.contains(active);
      const wrapBack = event.shiftKey && (active === first || active === heading.current || !inside);
      const wrapForward = !event.shiftKey && (active === last || !inside);
      if (wrapBack || wrapForward) { event.preventDefault(); (event.shiftKey ? last : first).focus(); }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  return createPortal(<div ref={root} className="fixed inset-0 z-50 h-dvh">
    <div className="absolute inset-0 bg-black/40" onClick={onClose} aria-hidden="true" />
    <div ref={panel} role="dialog" aria-modal="true" aria-labelledby={titleId} data-variant={variant}
      className={`absolute flex flex-col overflow-hidden border-slate-300 bg-white pb-[env(safe-area-inset-bottom)] ${PANEL[variant] || PANEL.full}`}>
      <div className="flex shrink-0 items-start justify-between gap-3 px-4 pt-4 pb-2">
        <h2 id={titleId} ref={heading} tabIndex={-1}
          className="font-display text-xl font-light leading-snug text-forest-800 focus:outline-none">{title}</h2>
        <button type="button" onClick={onClose} className="min-h-11 shrink-0 px-2 text-[13px] text-slate-600 underline underline-offset-2">Close</button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 pb-4">{children}</div>
    </div>
  </div>, document.body);
}
