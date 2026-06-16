import { useEffect, useRef } from 'react';

// Move focus to a ref'd element whenever a watched value changes — e.g. focus
// the detail pane after the selected paper changes so keyboard nav stays in the
// right place (Accessibility: keep focus where the user's attention moved).
//
// Returns the ref to attach to the focus target (or pass your own as the second
// arg). Skips the very first run so an initial mount doesn't steal focus; only
// subsequent changes to `value` trigger a focus.
//
//   const paneRef = useFocusOnChange(selectedKey);
//   <section ref={paneRef} tabIndex={-1}>…</section>
export function useFocusOnChange(value, externalRef) {
  const internalRef = useRef(null);
  const ref = externalRef || internalRef;
  const firstRun = useRef(true);

  useEffect(() => {
    if (firstRun.current) {
      firstRun.current = false;
      return;
    }
    const el = ref.current;
    if (el && typeof el.focus === 'function') {
      el.focus();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);

  return ref;
}

export default useFocusOnChange;
