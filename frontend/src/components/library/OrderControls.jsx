import { useState } from 'react';
import {
  PRESETS, SIGNALS, SIGNAL_LABEL, SLIDER_MAX, SLIDER_STEP, effectiveShares, orderShares, selectPreset,
} from '../../utils/readNextOrder.js';
import { SORT_FIELDS, SORT_DEFAULT_DIR, DEFAULT_SORT } from '../../utils/relevanceBands.js';
import { ORDER_INFO, coverageNote, orderCaption } from './readNextCopy.js';

const BTN = 'px-1.5 py-0.5 rounded-md border border-slate-300 text-[11px] text-slate-700 bg-white';

// ONE «Order» list: the presets, then the Zotero fields (same names as Zotero's columns). A field also
// writes the direction (arrow button); a preset puts the list back to the ranked order. With `reason`
// the presets are disabled and «Best match» (the server order) stands in as the way back.
export function OrderControl({ order, meta, sort, reason, onOrderChange, onSortChange }) {
  const [infoOpen, setInfoOpen] = useState(false);
  const field = sort.field !== DEFAULT_SORT.field;
  const value = field ? `field:${sort.field}` : reason ? 'best' : `preset:${order.preset}`;
  function choose(v) {
    const [kind, id] = v.split(':');
    if (kind === 'field') onSortChange({ field: id, dir: SORT_DEFAULT_DIR[id] });
    else {
      if (field || !id) onSortChange(DEFAULT_SORT);
      if (id) onOrderChange(selectPreset(order, id, meta));
    }
  }
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      <label className="inline-flex items-center gap-1 text-[11px] text-slate-500">
        Order
        <select
          value={value}
          onChange={(e) => choose(e.target.value)}
          className={BTN}
          aria-label="Order"
          aria-describedby={reason ? 'order-reason' : undefined}
        >
          {reason && <option value="best">Best match</option>}
          <optgroup label="Smart order">
            {PRESETS.map((p) => <option key={p.id} value={`preset:${p.id}`} disabled={Boolean(reason)}>{p.label}</option>)}
          </optgroup>
          <optgroup label="Zotero fields">
            {SORT_FIELDS.filter((f) => f.key !== DEFAULT_SORT.field).map((f) => <option key={f.key} value={`field:${f.key}`}>{f.label}</option>)}
          </optgroup>
        </select>
      </label>
      {field && (
        <button
          type="button"
          onClick={() => onSortChange({ ...sort, dir: sort.dir === 'asc' ? 'desc' : 'asc' })}
          className={`${BTN} hover:bg-slate-50`}
          title={sort.dir === 'asc' ? 'Ascending — click for descending' : 'Descending — click for ascending'}
          aria-label="Toggle sort direction"
        >
          {sort.dir === 'asc' ? '↑' : '↓'}
        </button>
      )}
      <button
        type="button"
        onClick={() => setInfoOpen((v) => !v)}
        aria-expanded={infoOpen}
        aria-label="What does Order change?"
        className="w-5 h-5 rounded-full text-[11px] leading-none text-slate-500 hover:bg-slate-100"
      >
        ⓘ
      </button>
      {reason && <span id="order-reason" className="text-[11px] text-slate-500">{reason}</span>}
      {infoOpen && <span role="note" className="basis-full text-[11px] text-slate-500">{ORDER_INFO}</span>}
    </span>
  );
}

// The state line: which shares act, and how many papers each signal is known for.
export function OrderCaption({ order, meta }) {
  const label = PRESETS.find((p) => p.id === order.preset)?.label || 'Balanced';
  return (
    <span data-testid="order-caption" className="basis-full text-slate-600">
      {orderCaption(label, orderShares(order, meta), meta)}
    </span>
  );
}

// Custom: four sliders (0–10), shown as shares of the signals that have data.
export function CustomPanel({ order, meta, onChange }) {
  const shares = effectiveShares(order.weights, meta);
  const allZero = SIGNALS.every((s) => !(shares[s] > 0));
  return (
    <div data-testid="order-custom-panel" className="mb-3 rounded-lg border border-slate-200 bg-white p-3 space-y-2">
      {SIGNALS.map((s) => {
        const empty = (meta?.coverage?.[s]?.present || 0) === 0;
        const note = coverageNote(meta, s);
        return (
          <div key={s} className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[12px] text-slate-700">
            <label htmlFor={`order-weight-${s}`} className="w-16 shrink-0">{SIGNAL_LABEL[s]}</label>
            <input
              id={`order-weight-${s}`}
              type="range"
              min={0}
              max={SLIDER_MAX}
              step={SLIDER_STEP}
              value={order.weights[s]}
              disabled={empty}
              onChange={(e) => onChange({ preset: 'custom', weights: { ...order.weights, [s]: Number(e.target.value) } })}
              className="min-w-0 flex-1 accent-teal-600"
              aria-valuetext={`${Math.round(shares[s] * 100)}%`}
            />
            <span className="mono w-10 shrink-0 text-right">{Math.round(shares[s] * 100)}%</span>
            {note && <span className="basis-full pl-[4.5rem] text-[11px] text-slate-500">{note}</span>}
          </div>
        );
      })}
      {allZero && <p className="text-[11px] text-amber-800">All weights are zero — papers keep their group and then go by date added.</p>}
    </div>
  );
}
