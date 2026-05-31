// Score-distribution histogram for the Library reading queue.
// X = relevance score (1–5, 0.5-wide bins), Y = count. Each bar is coloured by
// the band its bin falls in; the must-read band is highlighted (Von Restorff).
// Pure CSS bars — no chart dependency. Data: GET /api/library/reading-queue
// `distribution` (computed server-side over the full unread queue).

const BAND_BAR = {
  must_read: 'bg-emerald-500',
  should_read: 'bg-sky-500',
  could_read: 'bg-amber-500',
  dont_read: 'bg-rose-400',
};
const BAND_LABEL = {
  must_read: 'must',
  should_read: 'should',
  could_read: 'could',
  dont_read: "don't",
};

export default function ScoreHistogram({ distribution }) {
  if (!distribution || !Array.isArray(distribution.bins)) return null;
  const { bins, by_band = {}, total_scored = 0, unscored = 0, prestige_floor = null } = distribution;

  if (total_scored === 0) {
    return (
      <div className="mb-3 text-[11px] text-slate-400">
        No relevance scores yet — click <strong>Rescore</strong> to compute the distribution
        {unscored ? ` (${unscored} unscored)` : ''}.
      </div>
    );
  }

  const max = Math.max(1, ...bins.map((b) => b.count));

  return (
    <div className="mb-4 rounded-lg border border-slate-200 bg-white p-2.5">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 mb-1.5">
        <span className="text-[11px] font-semibold text-slate-600">
          Relevance to you · {total_scored} scored
          {unscored ? <span className="text-slate-400 font-normal"> · {unscored} unscored</span> : null}
          {prestige_floor != null ? (
            <span className="text-slate-400 font-normal" title="Top bands are quality-gated: a high-relevance paper with known low prestige counts one band lower. Bars show the score distribution; band counts reflect this floor.">
              {' '}· quality-floored
            </span>
          ) : null}
        </span>
        <span className="flex flex-wrap gap-2 text-[10px] text-slate-500">
          {Object.entries(BAND_LABEL).map(([band, lab]) => (
            <span key={band} className="flex items-center gap-1">
              <span className={`inline-block h-2 w-2 rounded-sm ${BAND_BAR[band]}`} />
              {lab} {by_band[band] || 0}
            </span>
          ))}
        </span>
      </div>

      <div className="flex items-end gap-1 h-24">
        {bins.map((b, i) => {
          const pct = Math.round((b.count / max) * 100);
          const must = b.band === 'must_read';
          return (
            <div
              key={i}
              className="flex-1 flex flex-col items-center justify-end h-full"
              title={`${b.lo.toFixed(1)}–${b.hi.toFixed(1)}: ${b.count}`}
            >
              <span className="text-[9px] text-slate-500 leading-none mb-0.5">{b.count || ''}</span>
              <div
                className={`w-full rounded-t ${BAND_BAR[b.band]} ${must ? 'ring-2 ring-emerald-700' : ''}`}
                style={{ height: `${b.count ? Math.max(4, pct) : 0}%` }}
              />
            </div>
          );
        })}
      </div>

      <div className="flex gap-1 mt-0.5">
        {bins.map((b, i) => (
          <span key={i} className="flex-1 text-center text-[8px] text-slate-400">
            {b.lo.toFixed(1)}
          </span>
        ))}
      </div>
    </div>
  );
}
