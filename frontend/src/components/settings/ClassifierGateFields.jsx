// Classifier-gate editor for Settings → Advanced. The Enable toggle stays
// visible; everything below it (model, floors, drop priorities) is hidden until
// the gate is enabled — progressive disclosure so the common "gate off" case
// isn't a wall of inert controls.

import { ALL_PRIORITIES } from '../../utils/configForm.js';
import { CheckboxField, Field } from '../form/Fields.jsx';

const CLASSIFIER_MODEL_OPTIONS = ['tabpfn', 'lightgbm', 'logreg'];

export default function ClassifierGateFields({ form, onUpdate, onToggleDropPriority }) {
  return (
    <div className="space-y-4">
      <CheckboxField
        label="Enable classifier gate"
        checked={form.gate_enabled}
        onChange={(v) => onUpdate('gate_enabled', v)}
        hint="Off keeps every dedup'd feed item flowing into the LLM (slower, more accurate)."
      />

      {/* Sub-fields hidden until the gate is enabled. */}
      {form.gate_enabled && (
        <>
          {/* Only the model choice is user-facing. The raw-score dont_read floor
              and audit-sample knobs were removed — ML-tuning the clinician can't
              reason about; their server defaults (0 / 1) round-trip untouched. */}
          <Field
            kind="select"
            label="Classifier model"
            value={form.gate_model_name}
            onChange={(v) => onUpdate('gate_model_name', v)}
            options={CLASSIFIER_MODEL_OPTIONS}
            hint="tabpfn = best F1, slower. lightgbm = fast. logreg = baseline."
          />
          <fieldset>
            <legend className="text-sm font-semibold text-slate-700 mb-2">Drop priorities</legend>
            <p className="text-xs text-slate-500 mb-2">
              Priorities the gate short-circuits. Items predicted into any checked bucket skip the LLM entirely.
            </p>
            <div className="flex flex-wrap gap-3">
              {ALL_PRIORITIES.map((priority) => (
                <label key={priority} className="flex items-center gap-2 cursor-pointer text-sm">
                  <input
                    type="checkbox"
                    checked={(form.gate_drop_priorities || []).includes(priority)}
                    onChange={() => onToggleDropPriority(priority)}
                    className="rounded"
                  />
                  <span className="font-mono">{priority}</span>
                </label>
              ))}
            </div>
          </fieldset>
        </>
      )}
    </div>
  );
}
