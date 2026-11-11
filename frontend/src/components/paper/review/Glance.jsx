import { BAND_LABEL, gradeBasis } from './tones.js';
import { metricView } from './metricModel.js';

// The paper at a glance, inside the verdict card: how well it is supported (grade,
// checks met, flags), the numbers it reports, and its strength next to its weakness.
// Every value is a stored review field shown as stored; nothing here is computed
// beyond counting cells and locating the number inside a reported metric.

const EYEBROW = 'text-[11px] font-medium uppercase tracking-[0.08em] text-slate-500';
const GRADE_INK = { A: 'text-forest-800', B: 'text-sky-700', C: 'text-amber-800', D: 'text-rose-700' };

// One cell per applicable check: met = Forest, not met = clay.
function ChecksBar({ met, applicable }) {
  return (
    <div role="img" aria-label={`${met} of ${applicable} checks met`} className="flex h-2 max-w-[14rem] gap-0.5">
      {Array.from({ length: applicable }, (_, i) => (
        <span key={i} className={`h-full flex-1 rounded-full ${i < met ? 'bg-forest-700' : 'bg-rose-300'}`} />
      ))}
    </div>
  );
}

function QualityTile({ grade, band, quality, redFlags }) {
  const met = Number(quality?.coverage_met) || 0;
  const applicable = Number(quality?.coverage_applicable) || 0;
  const basis = gradeBasis(quality?.coverage_met, quality?.coverage_applicable);
  const facts = [BAND_LABEL[band], quality?.type_uncertain ? 'type uncertain' : ''].filter(Boolean).join(' · ');
  return (
    <div className="min-w-0">
      <div className={EYEBROW}>Quality</div>
      <div className="mt-1 flex items-center gap-3">
        {grade && <span title="Saved quality grade" className={`font-display text-[40px] font-light leading-none ${GRADE_INK[grade] || 'text-slate-700'}`}>{grade}</span>}
        <div className="min-w-0 flex-1 space-y-1.5 text-[13px] leading-snug text-slate-700">
          {(basis || facts) && <div>{[basis, facts].filter(Boolean).join(' · ')}</div>}
          {basis && <ChecksBar met={met} applicable={applicable} />}
        </div>
      </div>
      {redFlags.length > 0 && (
        <div className="mt-2 text-[13px] leading-snug text-rose-700" title={redFlags.slice(0, 3).join('; ')}>
          ⚠ {redFlags.length} red flag{redFlags.length === 1 ? '' : 's'} to verify
        </div>
      )}
    </div>
  );
}

const TRACK = 'relative block h-1.5 overflow-hidden rounded-full bg-slate-200';
const FILL = 'absolute inset-y-0 rounded-full bg-forest-700';

function MetricVisual({ visual }) {
  if (!visual) return null;
  if (visual.kind === 'pair') {
    return (
      <span aria-hidden="true" className="mt-1.5 block max-w-[14rem] space-y-1">
        {visual.fills.map((fill, i) => (
          <span key={i} className={TRACK}><span className={`${FILL} left-0 ${i ? 'opacity-45' : ''}`} style={{ width: `${fill * 100}%` }} /></span>
        ))}
      </span>
    );
  }
  const from = visual.kind === 'range' ? visual.from : 0;
  const to = visual.kind === 'range' ? visual.to : visual.fill;
  return (
    <span aria-hidden="true" className={`${TRACK} mt-1.5 max-w-[14rem]`}>
      <span className={FILL} style={{ left: `${from * 100}%`, width: `${(to - from) * 100}%` }} />
    </span>
  );
}

// The first metric leads with its number set large; the reported string stays whole under it.
function MetricsTile({ metrics }) {
  const [hero, ...rest] = metrics.map((text) => ({ text, ...metricView(text) }));
  return (
    <div className="min-w-0">
      <div className={EYEBROW}>{metrics.length > 1 ? 'Metrics' : 'Key metric'} · as reported</div>
      <div className="mt-1">
        {hero.lead && <div className="font-display text-[30px] font-light leading-tight tabular-nums text-forest-800">{hero.lead}</div>}
        <div className="text-[13px] leading-snug text-slate-700">{hero.text}</div>
        <MetricVisual visual={hero.visual} />
      </div>
      {rest.length > 0 && (
        <ul className="mt-2 space-y-1.5">
          {rest.map((m, i) => (
            <li key={i} className="!text-[13px] !leading-snug text-slate-700">
              {m.before}<span className="font-medium tabular-nums text-forest-800">{m.lead}</span>{m.after}
              <MetricVisual visual={m.visual} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function ProsCons({ strength, weakness }) {
  const row = (mark, label, text, ink) => text && (
    <div className="grid grid-cols-[1.25rem_minmax(0,1fr)] items-baseline">
      <span aria-hidden="true" className={`font-medium ${ink}`}>{mark}</span>
      <div className="text-[14px] leading-snug text-slate-800"><span className="sr-only">{label}: </span>{text}</div>
    </div>
  );
  return (
    <div className="space-y-1.5">
      {row('+', 'Strength', strength, 'text-forest-700')}
      {row('−', 'Weakness', weakness, 'text-rose-700')}
    </div>
  );
}

/**
 * `strength` / `weakness` are ready nodes (the caller owns the "could not be grounded" state).
 * Renders nothing when the review has none of the three parts.
 */
export default function Glance({ digest, quality, grade, band, redFlags, isNonPaper, strength, weakness }) {
  const metrics = (digest?.parameters?.metrics || []).map((x) => String(x || '').trim()).filter(Boolean);
  const showQuality = !isNonPaper && Boolean(grade || BAND_LABEL[band] || redFlags.length
    || gradeBasis(quality?.coverage_met, quality?.coverage_applicable));
  if (!showQuality && !metrics.length && !strength && !weakness) return null;
  return (
    <div role="group" aria-label="At a glance" className="mt-3 space-y-3 border-t border-slate-200/70 pt-3">
      {(showQuality || metrics.length > 0) && (
        <div className="grid gap-x-6 gap-y-3 sm:grid-cols-2">
          {showQuality && <QualityTile grade={grade} band={band} quality={quality} redFlags={redFlags} />}
          {metrics.length > 0 && <MetricsTile metrics={metrics} />}
        </div>
      )}
      {(strength || weakness) && <ProsCons strength={strength} weakness={weakness} />}
    </div>
  );
}
