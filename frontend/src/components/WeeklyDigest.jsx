import { Link } from 'react-router-dom';

function TextList({ values, empty = 'Not recorded (not assessed)' }) {
  return values?.length ? <ul className="list-disc pl-5 space-y-1">{values.map((value, i) => <li key={i}>{value}</li>)}</ul> : <p>{empty}</p>;
}

function ExternalLink({ url, children }) {
  const unavailable = <span>{children}{children !== url && ' (link unavailable)'}</span>;
  try {
    if (!['https:', 'http:'].includes(new URL(url).protocol)) return unavailable;
  } catch { return unavailable; }
  return <a className="underline text-forest-800 break-all" href={url} target="_blank" rel="noopener noreferrer">{children}</a>;
}

function PaperLink({ id, children }) {
  return id ? <Link className="underline break-all" to={`/paper/${encodeURIComponent(id)}`}>{children}</Link> : <span>{children} (paper identity unrecorded)</span>;
}

function Card({ record, index }) {
  const { candidate, triage, card, provenance } = record;
  return <article className="rounded-2xl border border-slate-200 bg-white p-5 space-y-4 break-words min-w-0">
    <header>
      <p className="text-sm text-slate-600">{index + 1} · {card.worth_reading || 'unknown'} · {(triage.matched_projects || []).join(', ') || 'Project match unrecorded'}</p>
      <h3 className="font-display text-2xl text-forest-800">{candidate.title}</h3>
      <div className="flex flex-wrap gap-4 text-sm mt-2">
        <PaperLink id={candidate.source_id}>Open paper review</PaperLink>
        <ExternalLink url={candidate.url}>Original source</ExternalLink>
      </div>
    </header>
    <section><h4 className="font-medium">Why chosen</h4><p>{triage.rationale || 'Not recorded'}</p></section>
    <section><h4 className="font-medium">Core idea</h4><p>{card.core_idea || 'Not established'}</p></section>
    <section><h4 className="font-medium">Project use</h4><TextList values={card.project_use_notes} /></section>
    <section><h4 className="font-medium">Artifacts</h4>
      {['code', 'dataset', 'model'].map((kind) => <div key={kind} className="mt-1">
        <span className="font-medium">{kind === 'model' ? 'Weights/models' : kind}: </span>
        {card[`${kind}_urls`]?.length ? <ul>{card[`${kind}_urls`].map((url, i) => <li key={i}><ExternalLink url={url}>{url}</ExternalLink></li>)}</ul> : 'None verified — availability unknown'}
      </div>)}
    </section>
    <section><h4 className="font-medium">Reproducibility: {card.reproducibility_tier || 'unknown'}</h4>
      <p>{card.reproducibility_rationale || 'Effort not assessed'}</p>
      <p className="font-medium mt-2">Missing reproduction inputs</p><TextList values={card.missing_reproduction_inputs} />
      <p className="font-medium mt-2">Evidence gaps</p><TextList values={card.evidence_gaps} />
    </section>
    <details><summary className="cursor-pointer">Card details and provenance</summary>
      <p className="mt-2">{card.problem}</p><p>{card.engineering_novelty}</p>
      <p>Research / production impact / personal novelty (generated): {card.research_impact ?? 'unknown'} / {card.production_impact ?? 'unknown'} / {card.personal_novelty ?? 'unknown'}</p>
      <p>Source basis: {provenance?.basis || 'unknown'} · model: {provenance?.model || 'unrecorded'} · generated: {provenance?.generated_at || 'unrecorded'}</p>
      <p>Contribution: {(card.contribution_kind || []).join(', ') || 'unrecorded'} · Topics: {(card.topic_tags || []).join(', ') || 'unrecorded'}</p>
      <p>Benchmarks: {(card.benchmark_names || []).join(', ') || 'unrecorded'} · Project actions: {(card.project_uses || []).join(', ') || 'unrecorded'}</p>
      <pre className="whitespace-pre-wrap break-all text-xs mt-2">{JSON.stringify(provenance || {}, null, 2)}</pre>
    </details>
  </article>;
}

export default function WeeklyDigest({ report }) {
  const { metadata, cards, rejected_near_threshold: rejects, manual_full_text_required: missing, failed, research_ideas_by_project: ideas = {} } = report;
  return <div className="space-y-6 min-w-0">
    <header><h2 className="font-display text-2xl">Research Intelligence digest</h2>
      <p className="break-words">{metadata.from} → {metadata.to}</p>
      <p>{cards.length} cards · {metadata.counts.shortlisted} shortlisted · {metadata.counts.discovered} discovered</p>
      <p className="text-sm text-slate-600">Saved snapshot, not a live queue. Generated project suggestions are unverified; unknown is not a negative result.</p>
    </header>
    {!cards.length && <p>No engineering cards in this run. Check review/full-text needs, failures and selection details below.</p>}
    <div className="grid xl:grid-cols-2 gap-4">{cards.map((record, index) => <Card key={record.candidate.source_id} record={record} index={index} />)}</div>
    <section><h3 className="font-display text-xl">Research ideas by project</h3>
      {!Object.keys(ideas).length && <p>No structured ideas recorded.</p>}
      {Object.entries(ideas).map(([project, rows]) => <div key={project} className="mt-3 break-words"><h4 className="font-medium">{project}</h4>
        {!rows.length && <p>No structured ideas recorded.</p>}
        <ul className="list-disc pl-5">{rows.map((idea, i) => <li key={i} className="mt-2">
          <p>{idea.proposed_change}</p><p>Minimal validation: {idea.minimal_validation_experiment || 'Not recorded'}</p>
          <PaperLink id={idea.source_paper_id}>Source: {idea.source_paper_id}</PaperLink>
        </li>)}</ul></div>)}
    </section>
    <section><h3 className="font-display text-xl">Needs a current review / full text</h3>
      <p className="text-sm text-slate-600">A missing saved review does not prove the PDF is unavailable. Open the paper to check its current review and source.</p>
      {!missing.length && <p>None recorded.</p>}
      <ul>{missing.map((candidate) => <li key={candidate.source_id}><PaperLink id={candidate.source_id}>{candidate.title}</PaperLink></li>)}</ul>
    </section>
    <details><summary className="cursor-pointer">Why rejected near the threshold ({rejects.length})</summary>
      {!rejects.length && <p>No near-threshold rejects recorded (not a complete rejection audit).</p>}
      <ul className="space-y-2 mt-2">{rejects.map(({ candidate, triage }) => <li key={candidate.source_id} className="break-words"><ExternalLink url={candidate.url}>{candidate.title}</ExternalLink> — {triage.rationale || 'Reason not recorded'}</li>)}</ul>
    </details>
    <section><h3 className="font-display text-xl">Per-paper failures ({failed.length})</h3>
      {!failed.length ? <p>None recorded.</p> : <ul>{failed.map((row, i) => <li key={i} className="break-all"><PaperLink id={row.source_id}>{row.source_id}</PaperLink>: {row.error}</li>)}</ul>}
    </section>
    <details><summary className="cursor-pointer">Run metadata</summary>
      <pre className="whitespace-pre-wrap break-all text-xs mt-2">{JSON.stringify(metadata, null, 2)}</pre>
    </details>
  </div>;
}
