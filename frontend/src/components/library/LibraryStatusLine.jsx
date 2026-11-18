import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import Spinner from '../ui/Spinner.jsx';
import { libraryStatus } from './libraryStatus.js';

// The page's one status line under the tabs, plus the empty-list message. Both render a
// {text, action} from libraryStatus.js: errors announce at once (role="alert") and say «Error»,
// the rest wait their turn (role="status"); a warning says «Warning» — severity never rests on colour alone.
const TONE = {
  info: { box: 'border-slate-200 bg-slate-50 text-slate-700', word: '' },
  warning: { box: 'border-amber-200 bg-amber-50 text-amber-900', word: 'Warning' },
  error: { box: 'border-rose-200 bg-rose-50 text-rose-800', word: 'Error' },
};
const ACTION = 'inline-flex min-h-11 items-center rounded-md px-2 text-xs font-semibold underline underline-offset-2 hover:bg-white/60 disabled:opacity-50';
const SLOW_MS = 5000;  // «Connecting…» is neutral this long, then it says the app is slow

function Action({ action, handlers }) {
  if (!action) return null;
  if (action.to) return <Link to={action.to} className={ACTION}>{action.label}</Link>;
  return <button type="button" onClick={handlers[action.id]} className={ACTION}>{action.label}</button>;
}

function Message({ message, handlers }) {
  const tone = TONE[message.tone];
  return (
    <div
      data-testid="library-status"
      data-state={message.id}
      role={message.tone === 'error' ? 'alert' : 'status'}
      className={`my-2 flex flex-wrap items-center gap-x-2 rounded-lg border px-3 py-1.5 text-xs ${tone.box}`}
    >
      {message.busy && <Spinner size="sm" color="teal" />}
      <p className="min-w-0 flex-1 py-1">
        {tone.word && <strong className="mr-1">{tone.word}:</strong>}
        {message.text}
      </p>
      <Action action={message.action} handlers={handlers} />
    </div>
  );
}

// `ctx` is what libraryStatus() reads; `handlers` maps an action id to its click.
export default function LibraryStatusLine({ ctx, handlers }) {
  const first = libraryStatus({ ...ctx, slow: false });
  const [slow, setSlow] = useState(false);
  const connecting = first?.id === 'connecting';
  useEffect(() => {
    setSlow(false);
    if (!connecting) return undefined;
    const timer = setTimeout(() => setSlow(true), SLOW_MS);
    return () => clearTimeout(timer);
  }, [connecting]);
  const message = connecting && slow ? libraryStatus({ ...ctx, slow: true }) : first;
  return message ? <Message key={message.id} message={message} handlers={handlers} /> : null;
}

export function LibraryEmptyState({ state, handlers }) {
  if (!state) return null;
  return (
    <div data-testid="library-empty" data-state={state.id} role="status" className="my-6 text-center text-sm text-slate-600">
      <p>{state.text}</p>
      <Action action={state.action} handlers={handlers} />
    </div>
  );
}
