import { useCallback } from 'react';

// Generalized "advance the UI now, reconcile when the network lands" pattern,
// extracted verbatim from AnnotationVerdict's optimistic verdict save (Doherty
// Threshold: keep batch-mode flow under the 400 ms perceived ceiling).
//
// It returns a `run(variables, { context })` callback that:
//   1. calls `optimisticUpdate(variables, context)` immediately (e.g. advance to
//      the next paper, flash a "Saved" status)
//   2. fires the mutation via `mutate(variables, handlers)`
//   3. on success calls `onSuccess(data, variables, context)` (e.g. detect a
//      soft label/note write failure and re-flash) — the optimistic update is
//      kept
//   4. on error calls `rollback(error, variables, context)` (e.g. restore the
//      previously-selected key and flash the failure) so a failed write doesn't
//      silently leave the UI ahead of the server
//
// `mutate` is a React-Query mutation's `.mutate` (it owns isPending/error). The
// hook only sequences optimistic-update → mutate → success/rollback; it holds no
// state of its own.
export function useOptimisticAction({
  mutate,
  optimisticUpdate,
  rollback,
  onSuccess,
}) {
  return useCallback(
    (variables, { context } = {}) => {
      optimisticUpdate?.(variables, context);
      mutate(variables, {
        onSuccess: (data) => onSuccess?.(data, variables, context),
        onError: (error) => rollback?.(error, variables, context),
      });
    },
    [mutate, optimisticUpdate, rollback, onSuccess],
  );
}

export default useOptimisticAction;
