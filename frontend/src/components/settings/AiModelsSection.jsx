// Settings → AI Models region. The user's main complaint lives here, so it's
// FIRST (Serial Position): a read-only "what's running now" summary up top, and
// the single providers/stage-routing editor folded into one disclosure below.
// This is the ONLY place LLM config is edited — the old slim DefaultProviderField
// (a second editor for the same `default`) is gone (Occam / one entry point).
//
// `open` is controlled by the parent so the summary rows — and the readiness
// strip's LLM pill — can expand the editor and scroll it into view.

import { useEffect, useRef, useState } from 'react';
import ActiveModelsSummary from './ActiveModelsSummary.jsx';
import LlmRoutingSection from '../LlmRoutingSection.jsx';
import StepConnectLlm from '../setup/StepConnectLlm.jsx';

export default function AiModelsSection({ status, routing, onChange, isDirty, open, onToggle }) {
  const ref = useRef(null);
  const [testedOk, setTestedOk] = useState(false);

  useEffect(() => {
    if (open && ref.current) {
      ref.current.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  }, [open]);

  return (
    <section id="ai-models" className="glass rounded-2xl border border-slate-200 p-4 space-y-4 scroll-mt-20">
      <StepConnectLlm status={status} routing={routing} onPatchRouting={onChange}
        testedOk={testedOk} onTested={setTestedOk} />

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
          <span className="text-sm font-semibold text-slate-700">Advanced AI configuration</span>
        </summary>
        <div className="mt-4">
          <ActiveModelsSummary routing={routing} onEdit={() => {}} />
          <LlmRoutingSection value={routing} onChange={onChange} isDirty={isDirty} />
        </div>
      </details>
    </section>
  );
}
