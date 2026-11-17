import { useState } from 'react';
import {
  PRESETS, SIGNALS, SIGNAL_LABEL, SLIDER_MAX, SLIDER_STEP, effectiveShares, orderShares, selectPreset,
} from '../../utils/readNextOrder.js';
import { ORDER_INFO, coverageNote, orderCaption } from './readNextCopy.js';

// «Order» select next to Sort (Best match only) + the ⓘ scope note.
export function OrderSelect({ order, meta, onChange }) {
  const [infoOpen, setInfoOpen] = useState(false);
  return (
    <span className="inline-flex items-center gap-1">
      <label className="inline-flex items-center gap-1 text-[11px] text-slate-500">
        Order
        <select
          value={order.preset}
          onChange={(e) => onChange(selectPreset(order, e.target.value, meta))}
          className="px-1.5 py-0.5 rounded-md border border-slate-300 text-[11px] text-slate-700 bg-white"
          aria-label="Order"
        >
          {PRESETS.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
        </select>
      </label>
      <button
        type="button"
        onClick={() => setInfoOpen((v) => !v)}
        aria-expanded={infoOpen}
        aria-label="What does Order change?"
        className="w-5 h-5 rounded-full text-[11px] leading-none text-slate-500 hover:bg-slate-100"
      >
        ⓘ
      </button>
      {infoOpen && <span role="note" className="basis-full text-[11px] text-slate-500">{ORDER_INFO}</span>}
    </span>
  );
}

// The state line: which shares act, and how many papers each signal is known for.
export function OrderCaption({ order, meta }) {
  const label = PRESETS.find((p) => p.id === order.preset)?.label || 'Balanced';
  return (
    <span data-testid="order-caption" className="basis-full text-slate-600">
      {orderCaption(label, orderShares(order, meta), meta, order.preset === 'balanced')}
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
