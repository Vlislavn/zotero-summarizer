import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { fetchReviewFeedback, fetchReviewFeedbackChecks, withdrawReviewFeedbackPart } from '../../../api/libraryApi.js';
import FeedbackPanel from './FeedbackPanel.jsx';
import ReviewFeedback from './ReviewFeedback.jsx';
import { PART_LABELS, WHOLE, entriesOf } from './reviewFeedbackFields.js';

const queryKey = (itemKey) => ['review-feedback', itemKey];
const DESKTOP = '(min-width: 1024px)';
const PANEL_WIDTH = 384;
const PANEL_HEIGHT = 470;

// «Give feedback» mode: every part of the review (`[data-feedback-field]`, see reviewFeedbackFields.js) gets an outlined
// button laid over it, outside the review's own DOM, so no control is nested in another. The review itself is `inert`
// while the mode is on (its links and buttons do nothing) and closed folds are opened. Tapping a part opens its panel.
// Esc closes the panel first, then the mode; leaving the page unmounts it. `onActiveChange` tells the page the mode
// or a panel is up (the phone verdict bar steps aside).
function useTargets(on, wrap, content) {
  const [targets, setTargets] = useState([]);
  const measure = useCallback(() => {
    const origin = wrap.current?.getBoundingClientRect();
    if (!origin || !content.current) return;
    setTargets([...content.current.querySelectorAll('[data-feedback-field]')].map((el) => {
      const box = el.getBoundingClientRect();
      return { field: el.dataset.feedbackField, top: box.top - origin.top - 2, left: box.left - origin.left - 2, width: box.width + 4, height: box.height + 4 };
    }).filter((target) => target.width > 8 && target.height > 8));
  }, [wrap, content]);
  useLayoutEffect(() => {
    if (!on) { setTargets([]); return undefined; }
    const closed = [...content.current.querySelectorAll('[data-feedback-field]')].map((el) => el.closest('details'))
      .filter((details) => details && !details.open);
    closed.forEach((details) => { details.open = true; });
    measure();
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure);
    observer?.observe(content.current);
    window.addEventListener('resize', measure);
    return () => {
      observer?.disconnect();
      window.removeEventListener('resize', measure);
      closed.forEach((details) => { details.open = false; });
    };
  }, [on, measure, content]);
  return targets;
}

// Wide screens: the panel sits right of the part when it fits, else it falls back to the bottom-right card.
function placeBeside(target) {
  if (!target || !window.matchMedia?.(DESKTOP).matches) return null;
  const box = target.getBoundingClientRect();
  if (box.right + 16 + PANEL_WIDTH > window.innerWidth - 16) return null;
  return { left: box.right + 16, top: Math.max(72, Math.min(box.top, window.innerHeight - PANEL_HEIGHT - 16)) };
}

export default function FeedbackMode({ itemKey, reviewedAt, onActiveChange, children }) {
  const cache = useQueryClient();
  const [on, setOn] = useState(false);
  const [active, setActive] = useState(null);
  const [receipt, setReceipt] = useState('');
  const wrap = useRef(null);
  const content = useRef(null);
  const targets = useTargets(on, wrap, content);
  const query = useQuery({ queryKey: queryKey(itemKey), queryFn: () => fetchReviewFeedback(itemKey), staleTime: 60_000 });
  const entries = entriesOf((query.data?.feedback || []).find((row) => row.reviewed_at === reviewedAt));
  const checks = useQuery({ queryKey: ['review-feedback-checks', itemKey], queryFn: () => fetchReviewFeedbackChecks(itemKey),
    enabled: Object.keys(entries).length > 0, staleTime: 60_000, retry: false });

  useEffect(() => { onActiveChange?.(on || Boolean(active)); }, [on, active, onActiveChange]);
  useEffect(() => () => onActiveChange?.(false), [onActiveChange]);

  const closePanel = useCallback(() => {
    setActive((field) => {
      if (field) setTimeout(() => wrap.current?.querySelector(`[data-feedback-target="${field}"]`)?.focus(), 0);
      return null;
    });
  }, []);
  useEffect(() => {
    if (!on && !active) return undefined;
    const onKey = (event) => {
      if (event.key !== 'Escape') return;
      if (active) closePanel(); else setOn(false);
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [on, active, closePanel]);

  const store = (data, text) => {
    cache.setQueryData(queryKey(itemKey), (old) => {
      const rows = (old?.feedback || []).filter((row) => row.reviewed_at !== reviewedAt);
      return { ...old, item_key: itemKey, feedback: data.feedback ? [...rows, data.feedback] : rows };
    });
    cache.invalidateQueries({ queryKey: ['review-feedback-checks', itemKey] });
    setReceipt(text);
  };
  const withdraw = useMutation({
    mutationFn: (field) => withdrawReviewFeedbackPart(itemKey, field, reviewedAt),
    onSuccess: (data, field) => store(data, `Withdrawn · ${PART_LABELS[field]}`),
  });
  const preview = active && active !== WHOLE
    ? (content.current?.querySelector(`[data-feedback-field="${active}"]`)?.textContent || '').replace(/\s+/g, ' ').trim() : '';
  const place = active ? placeBeside(wrap.current?.querySelector(`[data-feedback-target="${active}"]`)) : null;

  const countOf = (field) => (entries[field] ? 1 : 0);
  return <>
    {on ? <div className="sticky top-0 z-30 -mx-1 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-amber-300 bg-amber-50/95 px-3 py-1.5 backdrop-blur">
      <span className="text-[13px] font-medium text-amber-900">Feedback mode · tap a part</span>
      <button type="button" onClick={() => { setActive(null); setOn(false); }}
        className="min-h-11 rounded-lg bg-forest-800 px-4 text-[13px] font-medium text-white">Done</button>
      <button type="button" onClick={() => setActive(WHOLE)} className="min-h-11 text-[13px] text-forest-800 underline underline-offset-2">Whole review</button>
    </div> : <div className="flex flex-wrap items-center justify-end gap-3">
      {receipt && <span className="mr-auto text-xs text-slate-600" aria-live="polite">{receipt}</span>}
      <button type="button" onClick={() => { setReceipt(''); setOn(true); }}
        className="min-h-11 rounded-lg border border-slate-300 bg-white px-3 text-[13px] text-slate-700 hover:bg-slate-50">Give feedback</button>
    </div>}
    {on && receipt && <p className="text-xs text-slate-600" aria-live="polite">{receipt}</p>}
    <div ref={wrap} className="relative">
      <div ref={content} inert={on ? '' : undefined}>{children}</div>
      {on && targets.map((target, index) => <button key={`${target.field}-${index}`} type="button" data-feedback-target={target.field}
        aria-label={`Give feedback on ${PART_LABELS[target.field]}`} onClick={() => setActive(target.field)}
        style={{ top: target.top, left: target.left, width: target.width, height: target.height }}
        className={`absolute z-20 rounded-lg border-2 border-dashed bg-amber-100/10 text-left hover:bg-amber-100/40 focus-visible:outline focus-visible:outline-2 focus-visible:outline-forest-700 ${
          active === target.field ? 'border-forest-700 bg-forest-100/30' : 'border-amber-500'}`}>
        <span aria-hidden="true" className="absolute -top-3 left-2 rounded bg-amber-500 px-1.5 text-[11px] font-medium leading-5 text-white">
          {PART_LABELS[target.field]}{countOf(target.field) ? ' ✓' : ''}
        </span>
      </button>)}
    </div>
    {query.isSuccess && <ReviewFeedback entries={entries} checks={checks.data?.checks} withdrawing={withdraw.isPending}
      onEdit={setActive} onWithdraw={(field) => withdraw.mutate(field)} />}
    {active && <FeedbackPanel key={active} itemKey={itemKey} reviewedAt={reviewedAt} field={active} entry={entries[active]}
      preview={preview} place={place} onClose={closePanel}
      onSaved={(data, text) => { store(data, text); closePanel(); }} />}
  </>;
}
