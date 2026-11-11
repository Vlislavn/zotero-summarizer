import { useState } from 'react';
import { humanizeError } from '../utils/humanizeError.js';
import Button from './ui/Button.jsx';
import Spinner from './ui/Spinner.jsx';
import { AUTH_CODES, CONSENT_TEXT, failureText, partialText, progressText, stepOf } from './telegramRunText.js';

const AUTH_COMMAND = 'zotero-summarizer research-feed telegram-auth';

export function AuthCommand() {
  const [copied, setCopied] = useState(false);
  return <div className="flex flex-wrap items-center gap-2">
    <code className="rounded-lg border border-slate-200 bg-white px-2 py-1 text-sm">{AUTH_COMMAND}</code>
    <Button size="sm" variant="secondary" onClick={() => {
      navigator.clipboard?.writeText(AUTH_COMMAND).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500); }, () => {});
    }}>{copied ? 'Copied' : 'Copy command'}</Button>
  </div>;
}

export function RunButton({ run }) {
  const { status, start, running, needsConsent } = run;
  const busy = running || start.isPending;
  return <Button className="min-h-11 shrink-0 disabled:!bg-sage-200 disabled:!text-forest-800 sm:min-h-10" disabled={busy || status.isPending} onClick={() => start.mutate(needsConsent)}>
    {busy ? <span className="inline-flex items-center gap-2"><Spinner size="xs" color="teal" />Checking…</span>
      : needsConsent ? 'Allow & check channels' : 'Check channels'}
  </Button>;
}

// Everything the run needs to say besides the button: consent line, progress, errors.
export function RunStatus({ run }) {
  const { status, value, start, running, needsConsent } = run;
  const code = start.error?.body?.error || (value?.state === 'error' ? value.error : null);
  const known = failureText(code, value?.retry_at);
  const warning = value?.state === 'reviewed' && value.retry_at ? partialText(value.retry_at) : null;
  return <>
    {needsConsent && <p className="rounded-xl border border-slate-200 p-3 text-sm leading-relaxed text-slate-700">{CONSENT_TEXT}</p>}
    {running && <div role="status" aria-live="polite" className="space-y-1.5 sm:max-w-md">
      <p className="text-sm text-slate-700">{progressText(value)}</p>
      <div aria-hidden="true" className="flex gap-1">{[1, 2, 3].map((step) =>
        <span key={step} className={`h-1 flex-1 rounded-full ${step <= stepOf(value.progress) ? 'bg-forest-800' : 'bg-sage-200'}`} />)}</div>
    </div>}
    {!running && (code || start.isError) && <div role="alert" className="space-y-2 rounded-xl border border-rose-200 bg-rose-50 p-3 text-sm text-rose-800">
      <p>{known || humanizeError(start.error)}</p>
      {AUTH_CODES.includes(code) && <AuthCommand />}
    </div>}
    {!running && warning && <p role="status" className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">{warning}</p>}
    {status.isError && <p role="alert" className="rounded-xl border border-rose-200 bg-rose-50 p-3 text-sm text-rose-800">Check status unavailable: {humanizeError(status.error)}</p>}
  </>;
}
