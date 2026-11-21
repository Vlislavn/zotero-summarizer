import { KeyVal, Bullets } from './primitives.jsx';

// A field whose text could not be grounded is stored empty (never shown as
// verified); one quiet line + the existing Regenerate action stands in its place.
export function Ungrounded({ onRegenerate }) {
  return <>
    <span className="text-slate-500">Could not be grounded this time</span>
    {onRegenerate && <>
      {' · '}
      <button type="button" onClick={onRegenerate} className="font-medium text-forest-800 underline underline-offset-2 hover:text-forest-700">Regenerate</button>
    </>}
  </>;
}

export function DigestRow({ field, lost, onRegenerate, children, ...rest }) {
  if (!lost.includes(field)) return <KeyVal {...rest}>{children}</KeyVal>;
  return <KeyVal {...rest} tone="default"><Ungrounded onRegenerate={onRegenerate} /></KeyVal>;
}

const LOST_ONLY = { relevance: 'Relevance', skip_parts: 'Skip', writing_reasons: 'Writing', read_parts: 'Read parts', verdict: 'Verdict' };

// The structured digest, behind the "Full digest" disclosure. Rendered ONCE
// (the old DigestBlock + iframe both showed it). Reading-scale KeyVal rows.
export function DigestRows({ digest, exclude = [], lost = [], onRegenerate = null }) {
  const d = Object.fromEntries(Object.entries(digest).filter(([key]) => !exclude.includes(key)));
  const p = d.parameters || {};
  const r = { lost: lost.filter((field) => !exclude.includes(field)), onRegenerate };
  return (
    <dl className="space-y-2">
      <DigestRow {...r} field="executive_summary" label="Summary">{d.executive_summary}</DigestRow>
      {(d.key_findings || []).filter(Boolean).length > 0 && (
        <KeyVal label="Key findings"><Bullets items={d.key_findings} /></KeyVal>
      )}
      <DigestRow {...r} field="methods" label="Methods">{d.methods}</DigestRow>
      <DigestRow {...r} field="parameters" label="Dataset / sample">{[p.dataset, p.sample_size].filter(Boolean).join(' · ')}</DigestRow>
      <KeyVal label="Architecture">{p.architecture}</KeyVal>
      {(p.baselines || []).filter(Boolean).length > 0 && <KeyVal label="Baselines"><Bullets items={p.baselines} /></KeyVal>}
      {(p.metrics || []).filter(Boolean).length > 0 && <KeyVal label="Metrics"><Bullets items={p.metrics} /></KeyVal>}
      {p.external_validation != null && <KeyVal label="External validation">{p.external_validation ? 'Yes' : 'No'}</KeyVal>}
      <DigestRow {...r} field="limitations" label="Limitations">{d.limitations}</DigestRow>
      <DigestRow {...r} field="controversies" label="Controversies">{d.controversies}</DigestRow>
      <DigestRow {...r} field="impact" label="Impact">{d.impact}</DigestRow>
      <DigestRow {...r} field="industry_impact" label="Industry">{d.industry_impact}</DigestRow>
      <DigestRow {...r} field="academy_impact" label="Academia">{d.academy_impact}</DigestRow>
      <DigestRow {...r} field="unknown_unknowns" label="Unknowns">{d.unknown_unknowns}</DigestRow>
      {(d.implementation || []).filter(Boolean).length > 0 && (
        <KeyVal label="Implementation"><Bullets items={d.implementation} /></KeyVal>
      )}
      <DigestRow {...r} field="implementation" label="Implementation" />
      <DigestRow {...r} field="key_strength" label="Strength" tone="pos">{d.key_strength}</DigestRow>
      <DigestRow {...r} field="key_weakness" label="Weakness" tone="neg">{d.key_weakness}</DigestRow>
      {Object.entries(LOST_ONLY).map(([field, label]) => <DigestRow key={field} {...r} field={field} label={label} />)}
      {/* The reviewer-LLM's self-reported 1-5 scores were removed: unvalidated
          opinion (the grade/band come from grounded checklist coverage, not these). */}
    </dl>
  );
}
