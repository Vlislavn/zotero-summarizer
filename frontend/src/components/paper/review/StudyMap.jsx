// The study as a schematic: what went in, what was done, what it was compared with and
// whether it was checked elsewhere — the saved `digest.parameters`, shown as stored.
// A vertical rail (pure CSS) reads the same at 390 px and inside the reading column.

const EYEBROW = 'text-[11px] font-medium uppercase tracking-[0.08em] text-slate-500';

function Step({ label, last, children }) {
  return (
    <li className={`relative pl-5 ${last ? '' : 'pb-4'}`}>
      {!last && <span aria-hidden="true" className="absolute bottom-0 left-[4px] top-2 w-px bg-forest-300" />}
      <span aria-hidden="true" className="absolute left-0 top-1.5 h-[9px] w-[9px] rounded-full bg-forest-700" />
      <div className={EYEBROW}>{label}</div>
      <div className="mt-0.5 text-[14px] leading-snug text-slate-800">{children}</div>
    </li>
  );
}

export function StudyFlow({ parameters }) {
  const p = parameters || {};
  const baselines = (p.baselines || []).filter(Boolean);
  const steps = [
    (p.dataset || p.sample_size) && ['Data', <>
      {p.dataset}
      {p.sample_size && p.sample_size !== p.dataset && <span className="block text-slate-600">Sample: {p.sample_size}</span>}
    </>],
    p.architecture && ['Method', p.architecture],
    ['Compared with', baselines.length
      ? <ul className="space-y-0.5">{baselines.map((b, i) => <li key={i}>{b}</li>)}</ul>
      : <span className="text-slate-500">No baselines listed in this review</span>],
    p.external_validation != null && ['External validation', p.external_validation ? 'Yes' : 'No'],
  ].filter(Boolean);
  if (!p.dataset && !p.architecture && !baselines.length) return null;
  return (
    <ol aria-label="Study at a glance" className="list-none">
      {steps.map(([label, body], i) => <Step key={label} label={label} last={i === steps.length - 1}>{body}</Step>)}
    </ol>
  );
}

// Who it matters to, as four peers: `cells` = [[label, node]] — empty ones are dropped.
export function ImpactGrid({ cells }) {
  const shown = cells.filter(([, body]) => body);
  if (!shown.length) return null;
  return (
    <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-2">
      {shown.map(([label, body]) => (
        <div key={label} className="min-w-0 border-t border-slate-200/70 pt-2">
          <dt className={EYEBROW}>{label}</dt>
          <dd className="mt-0.5 text-[14px] leading-snug text-slate-800">{body}</dd>
        </div>
      ))}
    </dl>
  );
}
