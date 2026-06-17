import { useState } from 'react';
import { Disclosure } from '../review/primitives.jsx';

// Two abstract renderers at reading scale:
//
//   variant="details"    — a shared Disclosure (its own "Abstract" summary),
//                          for the editable Decide column.
//   variant="expandable" — line-clamped to 6 lines + "Show more / less"; the
//                          parent Section already supplies the "Abstract" label,
//                          so no heading is repeated here.

export default function AbstractBlock({ abstract, variant = 'expandable' }) {
  const [expanded, setExpanded] = useState(false);
  if (!abstract) return null;

  if (variant === 'details') {
    return (
      <Disclosure summary="Abstract">
        <p className="text-[13px] leading-relaxed text-slate-700 max-h-60 overflow-y-auto whitespace-pre-line max-w-[66ch]">
          {abstract}
        </p>
      </Disclosure>
    );
  }

  return (
    <div className="max-w-[66ch]">
      <div
        className={`text-[14px] leading-relaxed text-slate-700 whitespace-pre-line ${
          expanded ? '' : 'line-clamp-6'
        }`}
      >
        {abstract}
      </div>
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="mt-1 text-[12px] text-teal-700 hover:text-teal-900 font-medium"
      >
        {expanded ? 'Show less' : 'Show more'}
      </button>
    </div>
  );
}
