import { useState } from 'react';

// One dismissible orientation hint — the teal "note" card that explains what a
// surface is FOR (Paradox of the Active User: a cold visitor never has to infer
// the workflow). Consolidates Today's `HintBanner` and Annotate's
// `AnnotateHintBanner`, which were the same card with different copy + a
// different localStorage key.
//
// Self-contained dismissal: it reads/writes its own `storageKey` in
// localStorage, so a parent renders <HintBanner storageKey=… text=… /> once and
// the banner manages "shown until dismissed, then never again" on its own. The
// dismiss is permanent (same as both originals). `className` lets a call site
// keep its exact bottom margin (Today used mb-4, Annotate mb-3).

function readDismissed(storageKey) {
  try {
    return window.localStorage.getItem(storageKey) === '1';
  } catch {
    return false;
  }
}

function writeDismissed(storageKey) {
  try {
    window.localStorage.setItem(storageKey, '1');
  } catch {
    /* no-op: incognito / disabled storage */
  }
}

export default function HintBanner({
  storageKey,
  children,
  className = 'mb-4',
  onDismiss,
}) {
  const [dismissed, setDismissed] = useState(() => readDismissed(storageKey));
  if (dismissed) return null;

  function handleDismiss() {
    writeDismissed(storageKey);
    setDismissed(true);
    onDismiss?.();
  }

  return (
    <div
      role="note"
      className={`flex items-start gap-3 p-3 rounded-xl border border-teal-200 bg-teal-50 text-sm text-teal-900 ${className}`}
    >
      <span className="flex-1 leading-snug">{children}</span>
      <button
        type="button"
        onClick={handleDismiss}
        aria-label="Dismiss hint"
        title="Dismiss"
        className="text-teal-700 hover:text-teal-900 leading-none px-1.5 py-0.5 rounded hover:bg-teal-100"
      >
        {'×'}
      </button>
    </div>
  );
}
