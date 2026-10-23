import { KeyVal, Bullets } from './primitives.jsx';

export default function ReadingPlan({ digest, decision }) {
  const opensOriginal = decision !== 'skip';
  const targets = (digest.read_parts || []).filter(part => part.trim());
  const skips = (digest.skip_parts || []).filter(part => part.trim());
  if (!opensOriginal && !skips.length) return null;

  return (
    <div role="region" aria-label="Reading plan" className="mt-3 min-w-0 break-words [&_dt]:text-slate-600">
      <dl className="space-y-2">
        {opensOriginal && <KeyVal label="Read parts">
          {targets.length ? <ol aria-label="Read parts" className="list-decimal pl-5 space-y-0.5">
            {targets.map((target, i) => <li key={i}>{target}</li>)}
          </ol> : 'Exact reading targets not recorded in this saved review.'}
        </KeyVal>}
        {skips.length > 0 && <KeyVal label="Skip parts"><Bullets items={skips} /></KeyVal>}
        {opensOriginal && <KeyVal label="What the original adds">
          {digest.original_value || 'Additional value not recorded in this saved review.'}
        </KeyVal>}
      </dl>
    </div>
  );
}
