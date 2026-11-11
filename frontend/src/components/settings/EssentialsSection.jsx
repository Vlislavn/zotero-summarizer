import { useEffect, useRef } from 'react';
import { Field } from '../form/Fields.jsx';
import Button from '../ui/Button.jsx';

// One goal per row over the same newline-joined draft text the single Save already writes
// (splitLines drops blank rows, so goals.yaml and its validation are untouched).
function GoalRows({ text, onChange, hint }) {
  const rows = text === '' ? [''] : text.split('\n');
  const last = useRef(null);
  const added = useRef(false);
  useEffect(() => { if (added.current) { added.current = false; last.current?.focus(); } });
  const emit = next => onChange(next.join('\n'));
  return <fieldset className="space-y-3 min-w-0">
    <legend className="text-sm font-medium text-slate-900">Goals</legend>
    <p className="text-xs text-slate-600">One research goal per row. Blank rows are dropped when you save.</p>
    {hint && <p className="text-xs text-slate-600">{hint}</p>}
    <ol className="space-y-2">{rows.map((goal, index) => <li key={index} className="flex items-start gap-2">
      <textarea ref={index === rows.length - 1 ? last : undefined} aria-label={`Goal ${index + 1}`} rows={2} value={goal}
        onKeyDown={event => { if (event.key === 'Enter') event.preventDefault(); }}
        onChange={event => emit(rows.flatMap((row, at) => at === index ? event.target.value.split('\n') : [row]))}
        className="min-w-0 flex-1 p-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-teal-500" />
      <Button variant="secondary" aria-label={`Remove goal ${index + 1}`} disabled={rows.length === 1 && !goal}
        onClick={() => emit(rows.filter((_, at) => at !== index))}>Remove</Button>
    </li>)}</ol>
    <Button variant="secondary" disabled={!rows[rows.length - 1].trim()} onClick={() => { added.current = true; emit([...rows, '']); }}>Add goal</Button>
  </fieldset>;
}

export default function EssentialsSection({ form, onUpdate, tidied = 0 }) {
  return <div id="essentials-goals" className="space-y-5">
    <GoalRows text={form.research_goals_text} onChange={value => onUpdate('research_goals_text', value)}
      hint={tidied ? `Stray quotes and commas were hidden on ${tidied} line${tidied === 1 ? '' : 's'}. The saved file keeps them until you save this page.` : undefined} />
    <Field kind="textarea" label="Triage criteria (one per line)" value={form.triage_criteria_text}
      onChange={value => onUpdate('triage_criteria_text', value)} rows={5}
      hint="Criteria used when assigning article relevance. Also used to pick Telegram posts." />
  </div>;
}
