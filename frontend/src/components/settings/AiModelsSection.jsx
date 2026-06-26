// Settings → AI Models region. The user's main complaint lives here, so it's
// FIRST (Serial Position): a read-only "what's running now" summary up top, and
// the single providers/stage-routing editor folded into one disclosure below.
// This is the ONLY place LLM config is edited — the old slim DefaultProviderField
// (a second editor for the same `default`) is gone (Occam / one entry point).
//
// `open` is controlled by the parent so the summary rows — and the readiness
// strip's LLM pill — can expand the editor and scroll it into view.

import { useEffect, useRef } from 'react';
import ActiveModelsSummary from './ActiveModelsSummary.jsx';
import LlmRoutingSection from '../LlmRoutingSection.jsx';

export default function AiModelsSection({ routing, onChange, isDirty, open, onToggle }) {
  const ref = useRef(null);

  useEffect(() => {
    if (open && ref.current) {
      ref.current.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  }, [open]);

  return (
    <section id="ai-models" className="glass rounded-2xl border border-slate-200 p-4 space-y-4 scroll-mt-20">
      <div>
        <h3 className="text-sm font-semibold uppercase tracking-wider text-slate-500">AI models</h3>
        <p className="text-xs text-slate-500 mt-1">
          Which LLM scores your feed, drains the backlog, and writes deep reviews —
          and how hard each one thinks.
        </p>
      </div>

      <ActiveModelsSummary routing={routing} onEdit={() => onToggle?.(true)} />

      <details
        ref={ref}
        open={open}
        onToggle={(e) => onToggle?.(e.currentTarget.open)}
        className="rounded-xl border border-slate-200 bg-white/40 p-3 scroll-mt-20"
      >
        <summary className="cursor-pointer select-none list-none flex items-center gap-2">
          <span
            className={`text-slate-400 text-xs transition-transform ${open ? 'rotate-90' : ''}`}
            aria-hidden
          >
            ▸
          </span>
          <span className="text-sm font-semibold text-slate-700">Edit providers &amp; routing</span>
          <span className="text-xs text-slate-400 font-normal">
            register endpoints · route each stage · temperature · thinking
          </span>
        </summary>
        <div className="mt-4">
          <LlmRoutingSection value={routing} onChange={onChange} isDirty={isDirty} />
        </div>
      </details>
    </section>
  );
}
