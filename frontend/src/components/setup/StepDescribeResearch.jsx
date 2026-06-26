// Wizard step 3 — Describe research. Collects research goals (one per line),
// triage criteria (prefilled with a sensible default the user can edit), and
// the output language. Inline field errors come from POST
// /api/setup/validate-config — we map field_errors[].loc onto the matching
// textarea so the user sees what to fix without leaving the step.

import { Field } from '../form/Fields.jsx';

// Map a validate-config field-error `loc` array onto the form key it touches.
// The backend errors are keyed by GoalsConfig field name (research_goals,
// triage_criteria, output_language); join messages for the same field.
export function errorsForField(fieldErrors, configKey) {
  if (!Array.isArray(fieldErrors)) return null;
  const msgs = fieldErrors
    .filter((e) => Array.isArray(e?.loc) && e.loc.includes(configKey))
    .map((e) => e.msg)
    .filter(Boolean);
  return msgs.length ? msgs.join('; ') : null;
}

export default function StepDescribeResearch({ draft, onPatchDraft, fieldErrors }) {
  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-base font-semibold text-slate-900">Describe your research</h3>
        <p className="text-sm text-slate-500 mt-1">
          These drive goal-alignment scoring. Free text — one goal per line.
        </p>
      </div>

      <Field
        kind="textarea"
        label="Research goals (one per line)"
        value={draft.research_goals_text}
        onChange={(v) => onPatchDraft({ research_goals_text: v })}
        rows={6}
        placeholder={'e.g. Foundation models for computational pathology\nClinical agents for oncology decision support'}
        error={errorsForField(fieldErrors, 'research_goals')}
        hint="What are you actually researching? The triage prompt scores feed items against these."
      />

      <Field
        kind="textarea"
        label="Triage criteria (one per line)"
        value={draft.triage_criteria_text}
        onChange={(v) => onPatchDraft({ triage_criteria_text: v })}
        rows={5}
        error={errorsForField(fieldErrors, 'triage_criteria')}
        hint="Hard/soft criteria the LLM weighs when scoring relevance. A sensible default is prefilled — edit freely."
      />
      {/* Output language defaults to English (configForm round-trips it); change
          it in Settings → Advanced if you need another — not a first-run decision. */}
    </div>
  );
}
