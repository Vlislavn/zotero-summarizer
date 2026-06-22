// Settings → Advanced. A single collapsible <details> (collapsed by default)
// that holds everything a power user occasionally needs: the full LLM
// providers/stage-routing editor (verbatim), the classifier gate, and the
// corpus-similarity slider. Re-chunking the formerly-flat page into
// Essentials + this one disclosure keeps the default surface short (Hick's Law)
// without removing any capability.
//
// `open` is controlled by the parent so the ReadinessStrip / DefaultProviderField
// "open Advanced" links can expand it and scroll the routing editor into view.

import { useEffect, useRef } from 'react';
import LlmRoutingSection from '../LlmRoutingSection.jsx';
import ClassifierGateFields from './ClassifierGateFields.jsx';

export default function AdvancedSection({
  form,
  isDirty,
  open,
  onToggle,
  onUpdate,
  onToggleDropPriority,
}) {
  const ref = useRef(null);

  // When opened programmatically, bring the section into view.
  useEffect(() => {
    if (open && ref.current) {
      ref.current.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  }, [open]);

  return (
    <details
      ref={ref}
      open={open}
      onToggle={(e) => onToggle?.(e.currentTarget.open)}
      className="glass rounded-2xl border border-slate-200 p-4 scroll-mt-20"
    >
      <summary className="cursor-pointer select-none list-none flex items-center gap-2">
        <span
          className={`text-slate-400 text-xs transition-transform ${open ? 'rotate-90' : ''}`}
          aria-hidden
        >
          ▸
        </span>
        <span className="text-sm font-bold uppercase tracking-wider text-slate-500">
          Advanced
        </span>
        <span className="text-xs text-slate-400 font-normal normal-case">
          LLM routing · classifier gate
        </span>
      </summary>

      <div className="mt-4 space-y-6">
        {/* LLM providers & stage routing — verbatim. */}
        <section id="advanced-llm-routing" className="space-y-2 scroll-mt-20">
          <div>
            <h4 className="text-sm font-bold uppercase tracking-wider text-slate-500">
              LLM providers &amp; stage routing
            </h4>
            <p className="text-xs text-slate-500 mt-1">
              Register OpenAI-compatible / Anthropic providers, then route each pipeline
              stage. Configure the default once; stages inherit it unless overridden.
            </p>
          </div>
          <LlmRoutingSection
            value={form.llm_routing}
            onChange={(nextRouting) => onUpdate('llm_routing', nextRouting)}
            isDirty={isDirty}
          />
        </section>

        <div className="border-t border-slate-200" />

        {/* Classifier gate. */}
        <section className="space-y-2">
          <div>
            <h4 className="text-sm font-bold uppercase tracking-wider text-slate-500">
              Classifier gate
            </h4>
            <p className="text-xs text-slate-500 mt-1">
              Optional fast-reject layer. When enabled, the daemon trains a small
              classifier from the golden CSV and drops items in the configured priorities
              before they ever reach the LLM.
            </p>
          </div>
          <ClassifierGateFields
            form={form}
            onUpdate={onUpdate}
            onToggleDropPriority={onToggleDropPriority}
          />
        </section>
        {/* The corpus-similarity slider was removed: a raw cosine [-1,1] floor the
            clinician can't reason about. The -0.3 server default is round-tripped
            untouched (configForm still maps it); edit goals.yaml to tune it. */}
      </div>
    </details>
  );
}
