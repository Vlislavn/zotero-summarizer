import { pretty } from '../utils/priorityLabels.js';

// Renders the additive provenance chain for a paper's derived priority.
// Props: { provenance } — shape matches /api/golden/review-detail's `provenance`.

const PRIORITY_BADGE = {
  must_read: 'bg-emerald-100 text-emerald-800 border-emerald-200',
  should_read: 'bg-sky-100 text-sky-800 border-sky-200',
  could_read: 'bg-amber-100 text-amber-800 border-amber-200',
  dont_read: 'bg-rose-100 text-rose-800 border-rose-200',
};

function fmt(n, digits = 2) {
  if (typeof n !== 'number' || Number.isNaN(n)) return '—';
  return n.toFixed(digits);
}

export default function ProvenanceBreakdown({ provenance = null }) {
  if (!provenance) {
    return (
      <div className="text-xs text-slate-400 italic">No provenance data.</div>
    );
  }

  const additive = provenance.additive_scoring || {};
  const shortCircuits = provenance.short_circuits || {};
  const thresholds = provenance.thresholds || {};
  const flags = Array.isArray(provenance.flags) ? provenance.flags : [];
  const hardVetoEmojis = Array.isArray(shortCircuits.hard_veto_emojis)
    ? shortCircuits.hard_veto_emojis
    : [];
  const inTrash = Boolean(shortCircuits.in_trash_override);

  const emojiContribs = Array.isArray(additive.emoji_contributions)
    ? additive.emoji_contributions
    : [];

  const derivedPriority = provenance.derived_priority || 'could_read';
  const persistedPriority = provenance.persisted_priority;
  const derivedScore = provenance.derived_score;

  return (
    <div>
      <div className="flex flex-wrap items-center gap-2 mb-3">
        <span className="text-xs text-slate-500 uppercase tracking-wider font-bold">
          Derived priority
        </span>
        <span
          className={`px-2 py-0.5 rounded text-xs font-semibold border ${
            PRIORITY_BADGE[derivedPriority] ||
            'bg-slate-100 text-slate-700 border-slate-200'
          }`}
        >
          {pretty(derivedPriority)}
        </span>
        {typeof derivedScore === 'number' && (
          <span className="mono text-xs text-slate-700">
            score {fmt(derivedScore)}
          </span>
        )}
        {persistedPriority && persistedPriority !== derivedPriority && (
          <span
            className={`px-2 py-0.5 rounded text-[11px] border ${
              PRIORITY_BADGE[persistedPriority] ||
              'bg-slate-100 text-slate-700 border-slate-200'
            }`}
            title="Persisted priority differs from derived"
          >
            persisted: {pretty(persistedPriority)}
          </span>
        )}
        {provenance.is_direct_user_verdict && (
          <span className="px-2 py-0.5 rounded text-[11px] border bg-emerald-50 text-emerald-700 border-emerald-200">
            user verdict
          </span>
        )}
        {provenance.is_manual_override && (
          <span className="px-2 py-0.5 rounded text-[11px] border bg-violet-50 text-violet-700 border-violet-200">
            manual override
          </span>
        )}
        {inTrash && (
          <span className="px-2 py-0.5 rounded text-[11px] border bg-rose-50 text-rose-800 border-rose-200">
            in trash
          </span>
        )}
        {hardVetoEmojis.map((e) => (
          <span
            key={e}
            className="px-2 py-0.5 rounded text-[11px] border bg-rose-50 text-rose-800 border-rose-200"
            title="Hard-veto emoji"
          >
            veto {e}
          </span>
        ))}
        {flags.map((f) => (
          <span
            key={f}
            className="px-2 py-0.5 rounded text-[11px] border bg-violet-50 text-violet-700 border-violet-200"
          >
            {f}
          </span>
        ))}
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-slate-500 text-left">
              <th className="pb-1 font-medium">Step</th>
              <th className="pb-1 font-medium text-right">Raw</th>
              <th className="pb-1 font-medium text-right">Capped</th>
              <th className="pb-1 font-medium text-right">Decayed</th>
            </tr>
          </thead>
          <tbody>
            <tr className="border-t border-slate-100">
              <td className="py-1 text-slate-800 font-medium">baseline</td>
              <td className="py-1 text-right mono text-slate-600">
                {fmt(additive.baseline)}
              </td>
              <td className="py-1 text-right mono text-slate-400">—</td>
              <td className="py-1 text-right mono text-slate-400">—</td>
            </tr>
            {emojiContribs.map((c, i) => (
              <tr key={`${c.emoji}-${i}`} className="border-t border-slate-100">
                <td className="py-1 text-slate-800">
                  <span className="text-base mr-1 align-middle">{c.emoji}</span>
                  <span className="align-middle text-slate-600">
                    {c.description || c.tier || ''}
                  </span>
                </td>
                <td className="py-1 text-right mono text-slate-600">
                  {fmt(c.raw_delta)}
                </td>
                <td className="py-1 text-right mono text-slate-400">—</td>
                <td className="py-1 text-right mono text-slate-900 font-semibold">
                  {fmt(c.decayed_delta)}
                </td>
              </tr>
            ))}
            <tr className="border-t border-slate-100">
              <td className="py-1 text-slate-800">
                annotations ({additive.annotation_count ?? 0})
              </td>
              <td className="py-1 text-right mono text-slate-600">
                {fmt(additive.annotation_score_raw)}
              </td>
              <td className="py-1 text-right mono text-slate-600">
                {fmt(additive.annotation_score_capped)}
              </td>
              <td className="py-1 text-right mono text-slate-900 font-semibold">
                {fmt(additive.annotation_decayed)}
              </td>
            </tr>
            <tr className="border-t border-slate-100">
              <td className="py-1 text-slate-800">
                notes ({additive.user_note_count ?? 0})
              </td>
              <td className="py-1 text-right mono text-slate-600">
                {fmt(additive.user_note_score_raw)}
              </td>
              <td className="py-1 text-right mono text-slate-600">
                {fmt(additive.user_note_score_capped)}
              </td>
              <td className="py-1 text-right mono text-slate-900 font-semibold">
                {fmt(additive.user_note_decayed)}
              </td>
            </tr>
            <tr className="border-t border-slate-100">
              <td className="py-1 text-slate-800">
                decay factor ({additive.days_since_added ?? 0}d)
              </td>
              <td className="py-1 text-right mono text-slate-400">—</td>
              <td className="py-1 text-right mono text-slate-400">—</td>
              <td className="py-1 text-right mono text-slate-700">
                ×{fmt(additive.decay_factor, 3)}
              </td>
            </tr>
            <tr className="border-t border-slate-100">
              <td className="py-1 text-slate-800">engagement sum</td>
              <td className="py-1 text-right mono text-slate-600">
                {fmt(additive.engagement_sum_raw)}
              </td>
              <td className="py-1 text-right mono text-slate-600">
                {fmt(additive.engagement_sum_capped)}
              </td>
              <td className="py-1 text-right mono text-slate-900 font-semibold">
                {fmt(additive.engagement_sum_decayed)}
              </td>
            </tr>
            <tr className="border-t-2 border-slate-300">
              <td className="py-1 text-slate-900 font-bold" colSpan={3}>
                final score
              </td>
              <td className="py-1 text-right mono text-slate-900 font-bold">
                {fmt(derivedScore)}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div className="mt-2 text-[11px] text-slate-500">
        Bins: dont&lt;{fmt(thresholds.dont_read_upper)} ≤ could&lt;
        {fmt(thresholds.could_read_upper)} ≤ should&lt;
        {fmt(thresholds.should_read_upper)} ≤ must
      </div>
    </div>
  );
}
