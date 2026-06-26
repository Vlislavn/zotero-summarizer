// Settings → Classifier region. The optional fast-reject classifier gate, in a
// collapsed-by-default disclosure (Hick's Law: keep the default surface short).
//
// This was the old "Advanced" disclosure that also held the LLM providers/stage
// routing — that moved to the AI Models region (AiModelsSection) so model config
// lives in one clearly-named place. What remains here is purely the classifier
// gate (a different mental model from the LLM endpoints).

import { useState } from 'react';
import ClassifierGateFields from './ClassifierGateFields.jsx';

export default function AdvancedSection({ form, onUpdate, onToggleDropPriority }) {
  const [open, setOpen] = useState(false);

  return (
    <details
      open={open}
      onToggle={(e) => setOpen(e.currentTarget.open)}
      className="glass rounded-2xl border border-slate-200 p-4 scroll-mt-20"
    >
      <summary className="cursor-pointer select-none list-none flex items-center gap-2">
        <span
          className={`text-slate-400 text-xs transition-transform ${open ? 'rotate-90' : ''}`}
          aria-hidden
        >
          ▸
        </span>
        <span className="text-sm font-semibold uppercase tracking-wider text-slate-500">
          Classifier gate
        </span>
        <span className="text-xs text-slate-400 font-normal normal-case">
          optional fast-reject layer
        </span>
      </summary>

      <div className="mt-4 space-y-2">
        <p className="text-xs text-slate-500">
          When enabled, the daemon trains a small classifier from the golden CSV and
          drops items in the configured priorities before they ever reach the LLM.
        </p>
        <ClassifierGateFields
          form={form}
          onUpdate={onUpdate}
          onToggleDropPriority={onToggleDropPriority}
        />
      </div>
    </details>
  );
}
