// Plain-English rendering of the distilled feedback report (GET /api/feedback-analysis/latest).
// Backend contract: zotero_summarizer/services/golden/feedback_analysis/summary.py.

const range = (low, high) => (low === null || high === null ? 'range unknown' : `${low}–${high}%`);
const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;

function Summary({ children }) {
  return <summary className="min-h-11 flex items-center gap-2 cursor-pointer text-sm text-forest-800"><span aria-hidden="true">▸</span>{children}</summary>;
}

function Channel({ channel }) {
  const { agreement } = channel;
  return <div className="space-y-2 py-3 border-t border-slate-200 first:border-t-0">
    <h5 className="text-sm font-medium text-slate-900">{channel.label}
      <span className="font-normal text-slate-600"> · {plural(channel.papers, 'paper')}, {plural(channel.observations, 'record')}</span></h5>
    {channel.transitions.length > 0 && <ul className="text-sm text-slate-700 space-y-0.5">
      {channel.transitions.map((t) => <li key={`${t.from}>${t.to}`}>
        {t.new ? `First recorded as ${t.to}` : `${t.from} → ${t.to}`} <span className="text-slate-500 tabular-nums">× {t.count}</span></li>)}
    </ul>}
    <p className="text-sm text-slate-700">{agreement
      ? `Agreed with the app's saved prediction for ${agreement.matched} of ${agreement.compared} (${range(agreement.low, agreement.high)}).`
      : 'No saved predictions to compare with yet.'}
      {channel.disagreements > 0 && ` ${plural(channel.disagreements, 'disagreement')} on record.`}</p>
    {channel.classes.length > 0 && <table className="w-full text-sm">
      <caption className="sr-only">Papers per class with 95% range</caption>
      <thead><tr className="text-left text-xs text-slate-500"><th className="font-normal py-1">Class</th>
        <th className="font-normal py-1 text-right">Papers</th><th className="font-normal py-1 text-right">Share of papers (95%)</th></tr></thead>
      <tbody>{channel.classes.map((c) => <tr key={c.name} className="border-t border-slate-100">
        <td className="py-1">{c.name}</td><td className="py-1 text-right tabular-nums">{c.papers}</td>
        <td className="py-1 text-right tabular-nums">{range(c.low, c.high)}</td></tr>)}</tbody>
    </table>}
  </div>;
}

function Period({ period, open }) {
  const body = period.channels.length
    ? period.channels.map((c) => <Channel key={c.label} channel={c} />)
    : <p className="text-sm text-slate-600 py-2">No decisions were recorded in this period.</p>;
  if (open) return <section aria-label={period.label}><h4 className="font-display text-lg font-light text-forest-800">{period.label}</h4>{body}</section>;
  return <details className="border-t border-slate-200 py-2"><Summary>{period.label}</Summary>{body}</details>;
}

export default function FeedbackAnalysisReport({ report }) {
  const [week, ...older] = report.periods;
  const gaps = report.gaps || [];
  return <div className="space-y-3">
    {report.status === 'insufficient_data' && <p role="status" className="text-sm text-amber-800">Too few decisions for firm conclusions yet. Treat the numbers below as a first look.</p>}
    <Period period={week} open />
    {older.map((p) => <Period key={p.id} period={p} />)}
    <p className="text-xs text-slate-600">Ranges are 95% intervals; a wide range means few papers. Based on {report.papers.toLocaleString()} papers
      {report.dates?.min ? ` seen ${report.dates.min.slice(0, 10)} – ${report.dates.max.slice(0, 10)}` : ''}. Descriptive only: it shows what you recorded,
      not that the model improved. Nothing in your labels or models was changed.</p>
    {gaps.length > 0 && <details className="border-t border-slate-200 py-2"><Summary>Data gaps ({gaps.length} kinds)</Summary>
      <ul className="text-sm text-slate-700 space-y-1">{gaps.map((g) => <li key={g.text}>{g.text} <span className="text-slate-500 tabular-nums">× {g.count.toLocaleString()}</span></li>)}</ul></details>}
  </div>;
}
