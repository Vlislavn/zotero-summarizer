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
          <div className="grid md:grid-cols-2 gap-4">
            <Field
              kind="select"
              label="Classifier model"
              value={form.gate_model_name}
              onChange={(v) => onUpdate('gate_model_name', v)}
              options={CLASSIFIER_MODEL_OPTIONS}
              hint="tabpfn = best F1, slower. lightgbm = fast. logreg = baseline."
            />
            <Field
              kind="number"
              label="Raw-score dont_read floor"
              value={form.gate_raw_score_dont_read_below}
              onChange={(v) => onUpdate('gate_raw_score_dont_read_below', v)}
              step={0.01}
              min={0}
              max={1}
              hint="Items with raw classifier prob < this cutoff get forced to dont_read. 0 disables."
            />
            <Field
              kind="number"
              label="Audit sample / tick"
              value={form.gate_audit_sample_per_tick}
              onChange={(v) => onUpdate('gate_audit_sample_per_tick', v)}
              step={1}
              min={0}
              max={20}
              hint="Counterfactual audit: resurrect N gate-rejected rows each tick so the user's verdict on them estimates false-negative rate. 0 disables."
            />
          </div>
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
