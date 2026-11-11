import { REVIEW_DEPTHS } from '../../utils/reviewDepth.js';
import { Disclosure } from '../paper/review/primitives.jsx';

export default function ReviewDepthChoice({ value, onChange }) {
  return <fieldset className="space-y-3 min-w-0">
    <legend className="sr-only">Review depth</legend>
    {Object.entries(REVIEW_DEPTHS).map(([key, option]) => <label key={key}
      className="flex min-h-11 cursor-pointer items-start gap-3 rounded-xl border border-slate-200 p-4 has-[:checked]:border-forest-800 has-[:checked]:bg-linen">
      <input type="radio" name="review_depth" value={key} checked={value === key} onChange={() => onChange(key)} className="mt-1" />
      <span className="min-w-0"><span className="block text-base font-medium text-slate-900">{option.label}{key === 'thorough' && ' (default)'}</span>
        <span className="block text-sm text-slate-600">{option.change}</span></span>
    </label>)}
    <div className="[&_summary]:min-h-11"><Disclosure summary="What was measured">
      <p className="text-sm text-slate-600">{REVIEW_DEPTHS.fast.measured}</p></Disclosure></div>
    <p className="text-sm text-slate-600">Reviews made under the other depth are shown as outdated, because the wording and grades can differ.</p>
  </fieldset>;
}
