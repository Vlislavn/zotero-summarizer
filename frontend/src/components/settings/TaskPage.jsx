import { useCallback, useEffect, useRef } from 'react';
import { Link, useBeforeUnload, useBlocker, useLocation } from 'react-router-dom';
import { editorIdentity } from '../../utils/settingsNavigation.js';

export default function TaskPage({ title, description, backTo, backLabel = 'Back', dirty = false, pending = false,
  leaveMessage = 'You have unsaved changes. Discard this draft and leave?', children }) {
  const heading = useRef(null);
  const location = useLocation();
  const protectedDraft = dirty || pending;
  const blocker = useBlocker(useCallback(({ currentLocation, nextLocation }) =>
    protectedDraft && editorIdentity(currentLocation) !== editorIdentity(nextLocation), [protectedDraft]));
  useEffect(() => {
    if (blocker.state !== 'blocked') return;
    const message = pending ? 'A request has already been sent and may finish after leaving. Leaving does not cancel it. Leave this task?' : leaveMessage;
    if (window.confirm(message)) blocker.proceed(); else blocker.reset();
  }, [blocker, leaveMessage, pending]);
  useBeforeUnload(useCallback(event => {
    if (!protectedDraft) return;
    event.preventDefault(); event.returnValue = '';
  }, [protectedDraft]));
  useEffect(() => { heading.current?.focus(); }, [title]);
  return <section className="max-w-3xl mx-auto space-y-5 pb-8 min-w-0 [&_button]:min-h-11 [&_button]:focus-visible:ring-2 [&_button]:focus-visible:ring-forest-800">
    <header className="space-y-3">
      <Link to={backTo} state={{ focusTask: new URLSearchParams(location.search).get('task') }} className="inline-flex min-h-11 items-center gap-2 text-sm text-forest-800 underline underline-offset-4"><span aria-hidden="true">←</span>{backLabel}</Link>
      <h1 ref={heading} tabIndex={-1} className="font-display text-3xl font-light text-forest-800 focus:outline-none">{title}</h1>
      {description && <p className="text-sm text-slate-600 leading-relaxed">{description}</p>}
    </header>
    {children}
  </section>;
}
