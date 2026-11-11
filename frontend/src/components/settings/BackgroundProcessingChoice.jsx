import { Field } from '../form/Fields.jsx';
import { PROCESSING_KNOBS, processingKnobValid } from '../../utils/configForm.js';

const FIELDS = [
  ['fleet_papers', 'Papers reviewed at once', 'More papers at once = faster preparation; lower it if your AI provider throttles. 0 uses the provider limit.'],
  ['calls_per_paper', 'Calls per paper', 'AI requests running in parallel inside one paper. 2 was fastest in tests; 0 uses the provider limit.'],
  ['backfill_days', 'Feed catch-up days', 'How far back a feed is read when it was not checked for a while.'],
  ['max_pages_per_feed', 'Pages per feed', 'The most pages read from one feed in a single check.'],
];

export default function BackgroundProcessingChoice({ form, onChange }) {
  return <fieldset className="grid gap-4 sm:grid-cols-2 min-w-0">
    <legend className="sr-only">Background processing</legend>
    {FIELDS.map(([key, label, hint]) => {
      const [, , fallback, min, max] = PROCESSING_KNOBS[key];
      return <Field key={key} kind="number" step={1} min={min} max={max} label={label} value={form[key]} onChange={value => onChange(key, value)}
        hint={`${hint} Range ${min}–${max}, default ${fallback}.`}
        error={processingKnobValid(key, form[key]) ? undefined : `Enter a whole number from ${min} to ${max}.`} />;
    })}
  </fieldset>;
}
