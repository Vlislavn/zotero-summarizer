import { Fragment, memo } from 'react';
import { cleanAbstract } from '../../utils/feedText.js';
import { isDecided, rowByline, rowSignals, stateLabel } from './readNextCopy.js';

// One Read-next row (card). Top to bottom: title → signal line → «AI summary» (the
// review's TL;DR) or the abstract clip → footer (authors · venue · year · PDF) → ONE state
// label. A decided row is compact: title + signals (+ your verdict). No right column and no
// value that lives only in a `title` tooltip, so it reads the same on a phone.

const TONE = {
  score: 'font-semibold text-teal-700',
  good: 'font-medium text-emerald-700',
  flag: 'font-medium text-rose-700',
  plain: 'text-slate-700',
  muted: 'text-slate-500',
};
const LABEL_CLS = {
  own: 'bg-amber-100 text-amber-900 border-amber-300 font-semibold',
  proposal: 'bg-indigo-50 text-indigo-700 border-indigo-200',
};

// Feed abstracts arrive as clipped HTML ("<p>Nature, Published online… <a hr"): show the
// text, without the tags or a tag cut off by the clip.
const clip = (text) => cleanAbstract(text).replace(/<\/?[a-z][^>]*$/i, '').trim();

// Inline flow, each signal unbreakable: a wrapped line ends on a «·», never starts with one.
function Signals({ signals, label }) {
  return (
    <span className="mt-0.5 text-[11px] leading-5">
      {signals.map((s, i) => (
        <Fragment key={s.key}>
          {i > 0 && '\u00A0· '}
          <span className={`whitespace-nowrap ${TONE[s.tone]}`}>{s.text}</span>
        </Fragment>
      ))}
      {label && <> {label}</>}
    </span>
  );
}

function ReadNextRow({
  item, expanded = false, ownChip = true, goalsStrong = false, modelReady = true,
  selectMode = false, checked = false, onToggleOpen, onToggleSelect,
}) {
  const compact = isDecided(item);
  const signals = rowSignals(item, { goalsStrong, modelReady });
  const state = stateLabel(item, ownChip);
  const label = state && (
    <span className={`inline-flex w-fit items-center rounded-full border px-1.5 text-[11px] leading-5 ${LABEL_CLS[state.kind]} ${compact ? 'ml-1' : 'mt-1'}`}>
      {state.text}
    </span>
  );
  const tldr = item.review_digest?.tldr;
  const summary = tldr || clip(item.abstract_preview);
  const { byline, year } = rowByline(item);
  const foot = [byline, year, item.has_pdf && 'PDF', item.read && 'Already read'].filter(Boolean);
  return (
    <div
      className={`flex w-full items-start gap-2 rounded-xl border bg-white hover:border-teal-300 hover:bg-teal-50/30 ${
        compact ? 'px-2.5 py-1.5' : 'p-2.5'
      } ${expanded ? 'border-teal-400 ring-1 ring-teal-200' : 'border-slate-200'}`}
    >
      {selectMode && (
        <input
          type="checkbox"
          checked={checked}
          onChange={() => onToggleSelect(item.item_key)}
          aria-label={`Select ${item.title || item.item_key}`}
          className="mt-1 h-4 w-4 rounded border-slate-300 text-teal-600 focus:ring-teal-500"
        />
      )}
      <button
        type="button"
        onClick={() => onToggleOpen(item.item_key)}
        aria-expanded={expanded}
        className="flex min-w-0 flex-1 flex-col text-left"
      >
        <span className="text-sm font-medium text-slate-900 line-clamp-2 sm:line-clamp-1">{item.title || '(untitled)'}</span>
        <Signals signals={signals} label={compact ? label : null} />
        {!compact && summary && (
          <span className="mt-1 text-xs text-slate-600 line-clamp-3 sm:line-clamp-2">
            {tldr && <span className="font-medium text-slate-500">AI summary: </span>}
            {summary}
          </span>
        )}
        {!compact && (
          <span className="mt-1 flex min-w-0 items-baseline gap-x-1 text-xs text-slate-500">
            {foot.map((text, i) => (
              <span key={text} className={`flex gap-x-1 ${i === 0 && byline ? 'min-w-0' : 'shrink-0 whitespace-nowrap'}`}>
                {i > 0 && <span aria-hidden="true">·</span>}
                <span className={i === 0 && byline ? 'truncate' : ''}>{text}</span>
              </span>
            ))}
          </span>
        )}
        {!compact && label}
      </button>
    </div>
  );
}

export default memo(ReadNextRow);
