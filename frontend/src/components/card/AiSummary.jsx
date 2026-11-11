// The card's AI summary row: a quiet “AI TL;DR” + one secondary “Generate AI TL;DR”. The first use asks ONE
// plain consent line; the consent token flow is unchanged (the server remembers
// it, so later taps generate at once). Once a TL;DR exists it is shown instead.

import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { generateCard } from '../../api/cardApi.js';
import Button from '../ui/Button.jsx';
import Icon from '../ui/Icon.jsx';
import Spinner from '../ui/Spinner.jsx';
import { aiReasonMessage } from './cardModel.js';

const ROW = 'flex min-h-11 flex-wrap items-center justify-between gap-x-3 gap-y-1 sm:min-h-9';
const BTN = 'min-h-11 sm:min-h-9';

// Terse labelled bullets (New / Take / Caveat / Not found), not prose (#127). The server's
// `Code` bullet only repeated the code badge under the TL;DR, so it is dropped; where the
// paper has a review, `notFound` (cardModel.notFoundLine) says what was NOT found instead.
const LABEL_TONE = { Caveat: 'text-amber-800', 'Not found': 'text-slate-600' };

function Summary({ bullets: stored, notFound }) {
  const bullets = [...stored.filter(({ label }) => label !== 'Code'),
    ...(notFound ? [{ label: 'Not found', text: notFound }] : [])];
  return (
    <div>
      <p className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-[0.06em] text-forest-800">
        <Icon name="summary" size={14} />AI TL;DR
      </p>
      {bullets.length === 0 && <p className="mt-1 text-[12px] text-slate-500">Nothing notable found.</p>}
      <ul className="mt-1 space-y-0.5">
        {bullets.map(({ label, text }) => (
          <li key={label} className="flex gap-2 text-[13px] leading-snug text-slate-800">
            <span className={`w-[4rem] shrink-0 text-[12px] font-medium ${LABEL_TONE[label] || 'text-forest-800'}`}>{label}</span>
            <span className="min-w-0">{text}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export default function AiSummary({ cardKey, card, notFound = '' }) {
  const queryClient = useQueryClient();
  const [asking, setAsking] = useState(false);
  const generate = useMutation({
    mutationFn: (token) => generateCard(cardKey, token),
    onSuccess: (data) => { setAsking(false); queryClient.setQueryData(['card', cardKey], data); },
  });
  if (Array.isArray(card.ai?.bullets)) return <Summary bullets={card.ai.bullets} notFound={notFound} />;

  const reason = card.ai_reason;
  const boundary = card.boundary || {};
  const label = (
    <span className="flex items-center gap-2 text-[13px] text-slate-600">
      <Icon name="summary" size={16} className="text-forest-800" />AI TL;DR
    </span>
  );
  if (reason === 'ai_disabled') {
    return <p className="flex items-center gap-2 text-[12px] text-slate-500"><Icon name="summary" size={16} />
      {aiReasonMessage(reason)} <Link to="/settings" className="text-forest-800 underline underline-offset-2">Enable</Link></p>;
  }
  if (reason === 'offline') return <p className="text-[12px] text-slate-500">{aiReasonMessage(reason)}</p>;

  const pending = generate.isPending;
  const needsConsent = reason === 'consent_required';
  const failed = reason === 'generation_failed' || generate.error;
  if (needsConsent && asking) {
    const where = boundary.local ? 'processed by a local model' : 'sent to an external model';
    return (
      <div className="rounded-lg bg-slate-50 px-3 py-2.5">
        <p className="text-[13px] leading-snug text-slate-700">
          The paper text will be {where}{boundary.provider ? ` (${boundary.provider})` : ''} to make the AI TL;DR. Allow?
        </p>
        <div className="mt-2 flex flex-wrap gap-2">
          <Button size="sm" className={BTN} disabled={pending} onClick={() => generate.mutate(boundary.token)}>
            {pending ? 'Generating…' : 'Allow & generate'}</Button>
          <Button size="sm" variant="ghost" className={BTN} disabled={pending} onClick={() => setAsking(false)}>Not now</Button>
        </div>
        {generate.error && <p className="mt-1 text-[11px] text-rose-700">Failed: {generate.error.message}</p>}
      </div>
    );
  }
  return (
    <div>
      <div className={ROW}>
        {label}
        <Button size="sm" variant="secondary" className={`${BTN} inline-flex items-center gap-1.5`} disabled={pending}
          onClick={() => (needsConsent ? setAsking(true) : generate.mutate(null))}>
          {pending && <Spinner size="xs" color="teal" />}
          {pending ? 'Generating…' : failed ? 'Retry AI TL;DR' : 'Generate AI TL;DR'}
        </Button>
      </div>
      {failed && <p className="text-[11px] text-rose-700">
        {generate.error ? `Failed: ${generate.error.message}` : aiReasonMessage(reason)}</p>}
      {['call_cap', 'daily_token_cap', 'circuit_open'].includes(reason) && <p className="text-[11px] text-slate-500">{aiReasonMessage(reason)}</p>}
    </div>
  );
}
