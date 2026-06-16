import { useState } from 'react';

// Two abstract renderers, kept byte-identical to the two call sites they came
// from so PaperDetailView is behaviour-preserving:
//
//   variant="expandable" — Annotate's version: line-clamped to 5 lines with a
//                          "Show more / Show less" toggle and an UPPERCASE
//                          "Abstract" heading. (was AnnotationVerdict_helpers
//                          AbstractBlock.)
//   variant="details"    — InlineAnnotate's version: a <details>/<summary>
//                          disclosure with a scrollable body. (was the inline
//                          block in InlineAnnotate.)

export default function AbstractBlock({ abstract, variant = 'expandable' }) {
  const [expanded, setExpanded] = useState(false);
  if (!abstract) return null;

  if (variant === 'details') {
    return (
      <details>
        <summary className="cursor-pointer text-[11px] uppercase tracking-wider font-semibold text-slate-500 select-none">
          Abstract
        </summary>
        <p className="mt-1 text-xs text-slate-700 max-h-44 overflow-y-auto whitespace-pre-line">
          {abstract}
        </p>
      </details>
    );
  }

  return (
    <div>
      <h3 className="text-xs font-bold uppercase tracking-wider text-slate-500 mb-1">
        Abstract
      </h3>
      <div
        className={`text-sm text-slate-700 whitespace-pre-line ${
          expanded ? '' : 'line-clamp-5'
        }`}
      >
        {abstract}
      </div>
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="mt-1 text-[11px] text-teal-700 hover:text-teal-900 font-medium"
      >
        {expanded ? 'Show less' : 'Show more'}
      </button>
    </div>
  );
}
