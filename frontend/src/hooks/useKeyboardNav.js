import { useEffect } from 'react';

// Reusable list keyboard-nav, generalized verbatim from AnnotationVerdict's
// inline handler (Jakob's Law: Gmail/Vim j/k to move, number keys to act).
//
// Behaviour:
//   - native controls, rich-text editors and ARIA widgets keep their own keys
//   - ignores composition and events already handled by another control
//   - ignores any chord with meta / ctrl / alt held
//   - 'j' → onNext(), 'k' → onPrev()  (preventDefault on both)
//   - a key present in `actionKeys` → onAction(actionKeys[key], key) once the
//     list has a selection (preventDefault)
//   - listens on window; re-binds when its deps change
//
// Params (single options object):
//   - onPrev, onNext: () => void   — move selection up / down
//   - onAction: (mapped, rawKey) => void — fired for an action key
//   - actionKeys: Record<string, unknown> — e.g. { 1: 'must_read', ... }
//   - hasSelection: boolean — gate action keys (no-op with nothing selected)
//   - enabled: boolean (default true) — master switch
//   - deps: unknown[] — extra deps so handlers see fresh closures
export function useKeyboardNav({
  onPrev,
  onNext,
  onAction,
  actionKeys = {},
  hasSelection = true,
  enabled = true,
  deps = [],
}) {
  useEffect(() => {
    if (!enabled) return undefined;
    function onKey(e) {
      if (e.defaultPrevented || e.isComposing) return;
      const control = e.target?.closest?.(
        'input, textarea, select, button, a[href], summary, audio[controls], video[controls], '
        + '[contenteditable]:not([contenteditable="false"]), '
        + '[role="textbox"], [role="searchbox"], [role="combobox"], [role="listbox"], '
        + '[role="slider"], [role="spinbutton"], [role="button"], [role="checkbox"], '
        + '[role="radio"], [role="switch"], [role="tab"], [role="menu"], [role="menubar"], '
        + '[role="menuitem"], [role="tree"], [role="treeitem"], [role="grid"]',
      );
      if (control) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;

      if (e.key === 'j') {
        e.preventDefault();
        onNext?.();
        return;
      }
      if (e.key === 'k') {
        e.preventDefault();
        onPrev?.();
        return;
      }
      if (Object.prototype.hasOwnProperty.call(actionKeys, e.key) && hasSelection) {
        e.preventDefault();
        onAction?.(actionKeys[e.key], e.key);
      }
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, onPrev, onNext, onAction, hasSelection, ...deps]);
}

export default useKeyboardNav;
