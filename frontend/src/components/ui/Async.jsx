import Spinner from './Spinner.jsx';
import { humanizeError } from '../../utils/humanizeError.js';

// One small wrapper for the loading / error / empty / loaded fork that every
// data-backed pane repeats by hand. Renders, in order:
//   - loading  → a spinner + `loadingText` (role="status", aria-live="polite")
//   - error    → a humanized error line (role="alert") in the rose vocab
//   - empty    → the `emptyMessage` (or `empty` node) in muted slate
//   - children → the loaded content
//
// `loading`/`error`/`empty` are the same booleans/values a React-Query or
// useState pane already has. `error` may be any thrown value — it is routed
// through `humanizeError` so the UI never shows a raw `HTTP 503:` or
// `[object Object]`. `empty` may be a boolean (use `emptyMessage`) or a node
// (rendered directly).
export default function Async({
  loading,
  error,
  empty,
  loadingText = 'Loading…',
  emptyMessage = 'Nothing here yet.',
  spinnerSize = 'sm',
  spinnerColor = 'teal',
  children,
}) {
  if (loading) {
    return (
      <div
        role="status"
        aria-live="polite"
        className="flex items-center gap-2 p-4 text-sm text-slate-600"
      >
        <Spinner size={spinnerSize} color={spinnerColor} />
        {loadingText}
      </div>
    );
  }
  if (error) {
    return (
      <div
        role="alert"
        className="my-2 p-2 rounded-lg bg-rose-50 border border-rose-200 text-xs text-rose-800"
      >
        {humanizeError(error)}
      </div>
    );
  }
  if (empty) {
    return typeof empty === 'boolean' ? (
      <div className="p-4 text-sm text-slate-500">{emptyMessage}</div>
    ) : (
      empty
    );
  }
  return children;
}
