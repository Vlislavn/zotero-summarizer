const CATEGORIES = { context: 'Context', news: 'Author’s news (claims)', discussion: 'Discussion opinions',
  contrasts: 'Contrasting arguments', evidence: 'Evidence / links as reported (not paper-verified)',
  caveats: 'Caveats / unproven points', implications: 'Source-supported implications' };
function Link({ href, children }) {
  const safe = typeof href === 'string' && /^https?:\/\//i.test(href);
  return safe ? <a className="underline text-forest-800 break-all" href={href} target="_blank" rel="noopener noreferrer">{children}</a> : <span>{children}</span>;
}
function sources(report) {
  const values = {};
  for (const batch of report.conversations || []) {
    if (batch.origin) values[`telegram:${batch.origin.channel_id}:${batch.origin.message_id}`] = batch.origin;
    for (const row of [...(batch.root ? [batch.root] : []), ...batch.messages]) values[`telegram:${row.peer_id}:${row.message_id}`] = row;
  }
  return values;
}
function Assertion({ value, originals }) {
  return <div className="space-y-2"><p className="whitespace-pre-wrap">{value.text}</p>
    <ul className="space-y-2 text-sm">{value.citations.map((cite, index) => <li key={index}>
      <Link href={originals[cite.source_id]?.permalink}>{cite.source_id}</Link>
      <blockquote className="border-l-2 border-sage-300 pl-3 whitespace-pre-wrap">{cite.quote}</blockquote>
      {originals[cite.source_id]?.urls?.map((url, i) => <p key={i}><Link href={url}>{url}</Link></p>)}
    </li>)}</ul>
  </div>;
}
export default function TelegramDigest({ report }) {
  const meta = report.metadata;
  const originals = sources(report);
  if (meta.schema_version === 1) return <section className="space-y-4" aria-label="Saved Telegram news">
    <h2 className="font-display text-2xl">Saved Telegram news</h2>
    <p>Historical v1 post-only report. No discussion coverage or topic mini-reviews were recorded. AI claims are not scientifically verified.</p>
    {(report.cards || []).map((card) => <article className="border rounded-xl p-4 space-y-2" key={card.source_id}>
      <h3><Link href={card.permalink}>{card.source_id}</Link></h3><p className="whitespace-pre-wrap">{card.summary}</p>
      <p>{card.reason}</p><blockquote className="whitespace-pre-wrap">{card.evidence_quote}</blockquote>
    </article>)}
    {!(report.cards || []).length && <p>No selected news cards in this snapshot.</p>}
    <h3>All retained post states</h3>
    {(report.posts || []).map((row) => <details key={row.source_id}><summary>{row.source_id}: {row.state} — {row.reason || row.verdict?.reason}</summary>
      <Link href={row.post.permalink}>Open source</Link><p className="whitespace-pre-wrap">{row.post.text}</p>
    </details>)}
  </section>;
  return <section className="space-y-5 min-w-0" aria-label="Telegram topic mini-reviews">
    <h2 className="font-display text-2xl text-forest-800">Telegram topic mini-reviews</h2>
    <p>{meta.scope}</p>
    <p className="text-sm">Saved historical snapshot · {meta.generated_at} · {meta.status}. AI-generated and scientifically unverified. Linked papers have not been independently checked.</p>
    <section className="space-y-2"><h3 className="font-display text-xl">Coverage and errors</h3>
      {(report.conversations || []).map((batch, index) => <div key={index} className="text-sm break-words space-y-1">
        <p>{batch.target.kind}: @{batch.target.username}{batch.origin ? ` / post ${batch.origin.message_id}` : ''}</p>
        {batch.target.kind === 'broadcast' && <p>{batch.post_window
          ? `Post window: scanned ${batch.post_window.scanned}; retained ${batch.post_window.retained}; lookahead ${batch.post_window.lookahead}; ${batch.post_window.truncated ? 'truncated' : 'not truncated'}`
          : 'Post window: not recorded.'}</p>}
        <p>{`Replies/messages: ${batch.status}; ${batch.messages.length} replies/messages; ${batch.text_count ?? 'unrecorded'} retained texts; scanned ${batch.scanned_count ?? 'unrecorded'}, nontext/skipped ${batch.skipped_count ?? 'unrecorded'}`}{batch.reported_reply_count != null ? `; reported total ${batch.reported_reply_count}` : ''}{batch.root_id ? `; thread ${batch.peer_id}:${batch.root_id}` : ''}
          {batch.attempted === false ? '; not attempted' : ''}{batch.reason ? ` (${batch.reason})` : ''}
        </p>
      </div>)}
      {(report.errors || []).map((error, i) => <p key={i} role="alert">{error.stage}: {error.code} ({error.type})</p>)}
    </section>
    {(report.topics || []).map((topic, index) => <article key={index} className="border border-slate-200 bg-white rounded-xl p-4 space-y-4">
      <h3 className="font-display text-xl text-forest-800">{topic.title}</h3>
      {Object.entries(CATEGORIES).map(([key, label]) => {
        const assertions = key === 'context' ? [topic.context] : topic[key];
        return <section key={key} className="space-y-2"><h4 className="font-medium">{label}</h4>
          {assertions.length ? assertions.map((value, i) => <Assertion key={i} value={value} originals={originals} />)
            : <p className="text-sm text-slate-500">Not observed/supported in this bounded snapshot.</p>}
        </section>;
      })}
    </article>)}
    {!(report.topics || []).length && <p>No topic mini-reviews in this snapshot. Collection-only, empty context and review failures are not negative scientific judgments.</p>}
    <details className="space-y-3"><summary className="cursor-pointer">Retained originals and reproduction identity</summary>
      <p className="text-sm">Provider: {meta.evaluation_identity?.provider || 'not invoked'} · model: {meta.evaluation_identity?.model || 'not invoked'} · prompt: {meta.evaluation_identity?.prompt_version || 'not invoked'}. Model revision / measured usage: unrecorded.</p>
      {Object.entries(originals).map(([id, row]) => <article key={id} className="border-t py-3 space-y-2">
        <h4><Link href={row.permalink}>{id}</Link></h4><p className="text-xs">Published {row.published_at}{row.updated_at ? ` · edited ${row.updated_at}` : ''}{row.skip_reason ? ` · ${row.skip_reason}` : ''}</p>
        <p className="whitespace-pre-wrap break-words">{row.text}</p>
      </article>)}
    </details>
  </section>;
}
