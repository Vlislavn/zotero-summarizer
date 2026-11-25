import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { buildMethodCard, fetchMethodCard } from '../../../api/libraryApi.js';
import Button from '../../ui/Button.jsx';
import Spinner from '../../ui/Spinner.jsx';

// The steps of the "How it works" fold (the fold, in PaperReview, supplies the heading and the digest's
// Methods/Limitations below): the paper's mechanism, what it was compared against, what the authors claim is new
// and how to repeat it — each row tied to a sentence that exists in the paper (opened by its §).
// Built on request (POST) and polled (GET) while the one background job runs; collapsed to the short
// level, "Expand" opens all four blocks. Nothing here scores or ranks.
const WHY_NOT = {
  model_error: 'The model call failed.',
  source_changed: 'The PDF changed since the review was made.',
  no_text: 'No readable text was found in the PDF.',
};
const UNAVAILABLE = {
  too_long: 'This paper is longer than the review model can read at once, so no card was built.',
  lean_provider: 'The lean local review model reads too little of a paper to build this card.',
  review_running: 'A review is running; build the card when it finishes.',
};

function Ref({ section, quote }) {
  return (
    <details className="mt-0.5 text-[13px]">
      <summary className="inline-flex min-h-6 cursor-pointer select-none list-none items-center rounded text-[12px] font-medium text-forest-800 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-forest-400 [&::-webkit-details-marker]:hidden">
        {section || 'quote'}
      </summary>
      <blockquote className="mt-1 border-l-2 border-slate-300 pl-3 text-slate-600">{`\u201C${quote}\u201D`}</blockquote>
    </details>
  );
}

function Block({ label, children }) {
  return (
    <div className="grid gap-x-4 gap-y-1 sm:grid-cols-[8rem_minmax(0,1fr)]">
      <dt className="text-[11px] font-medium uppercase tracking-[0.08em] text-slate-500 sm:pt-1">{label}</dt>
      <dd className="min-w-0 space-y-2">{children}</dd>
    </div>
  );
}

function Row({ children, row }) {
  return <div>{children}<Ref section={row.section} quote={row.quote} /></div>;
}

function CardBody({ card }) {
  const { core_idea: core, compared = [], novelty, reuse = [], caveat, use_when: useWhen } = card;
  return (
    <dl className="space-y-4">
      <Block label="Core idea"><Row row={core}><p>{core.text}</p></Row></Block>
      <Block label="Compared against">
        {compared.length === 0 && <p className="text-slate-500">No measured comparison with a named baseline was found in the paper.</p>}
        {compared.map((row) => <Row key={`${row.what}|${row.result}`} row={row}><p><span className="font-medium">{row.what}</span> {'\u2192'} {row.result}</p></Row>)}
        {caveat && <Row row={caveat}><p className="text-slate-600">Caveat: {caveat.text}</p></Row>}
        <p className="text-slate-500">Closest prior work: not checked yet</p>
      </Block>
      <Block label="What’s new">
        {novelty ? (
          <Row row={{ section: novelty.section, quote: novelty.claim_quote }}>
            <p>Authors claim: {`\u201C${novelty.claim_quote}\u201D`}</p>
          </Row>
        ) : <p className="text-slate-500">No sentence stating what is new was found in the paper.</p>}
        <p className="text-slate-500">Novelty check: not checked yet</p>
      </Block>
      <Block label="Reuse">
        {reuse.length === 0 && <p className="text-slate-500">No reproducible steps could be tied to the paper text.</p>}
        {reuse.length > 0 && (
          <ol className="list-decimal space-y-2 pl-5">
            {reuse.map((row) => (
              <li key={`${row.input}|${row.operation}`}>
                <Row row={row}><p>{row.input} {'\u2192'} {row.operation} {'\u2192'} {row.output}</p></Row>
              </li>
            ))}
          </ol>
        )}
      </Block>
      {useWhen && <Block label="Use it when"><p>{useWhen}</p></Block>}
    </dl>
  );
}

function useCountdown(seconds) {
  const [left, setLeft] = useState(seconds);
  useEffect(() => {
    setLeft(seconds);
    if (!(seconds > 0)) return undefined;
    const timer = setInterval(() => setLeft((value) => Math.max(0, value - 1)), 1000);
    return () => clearInterval(timer);
  }, [seconds]);
  return left;
}

export function MethodCardView({ data, onBuild, building = false, buildError = '' }) {
  const [expanded, setExpanded] = useState(false);
  const wait = useCountdown(data.retry_in_seconds || 0);
  const card = data.card;
  const canBuild = data.can_build && data.state !== 'ready' && !building;
  let body;
  if (data.state === 'ready' && card) {
    body = (
      <div className="space-y-3">
        {expanded ? <CardBody card={card} /> : <p>{card.short_text}</p>}
        <button type="button" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}
          className="min-h-11 rounded px-1 text-[13px] font-medium text-forest-800 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-forest-400">
          {expanded ? 'Collapse \u25B4' : 'Expand \u25BE'}
        </button>
        {expanded && <p className="text-[12px] text-slate-500">Written by the model from the paper text. Each row is tied to a sentence of the paper; open its § to read it.</p>}
      </div>
    );
  } else if (data.state === 'building' || building) {
    body = <p role="status" aria-live="polite" className="flex items-center gap-2 text-slate-600"><Spinner size="sm" color="slate" /> Building the card — reading the whole paper once. You can keep reading.</p>;
  } else if (data.state === 'failed' && data.reason === 'not_grounded') {
    // Not an error: the digest's Methods text sits right below in the same fold.
    body = <p className="text-slate-600">Step-by-step view unavailable for this paper.</p>;
  } else if (data.state === 'failed') {
    body = <p className="text-amber-800" role="alert">Could not build the card. {WHY_NOT[data.reason] || ''}</p>;
  } else if (UNAVAILABLE[data.reason]) {
    body = <p className="text-slate-600">{UNAVAILABLE[data.reason]}</p>;
  } else {
    body = <p className="text-slate-600">What the method does, what it was compared against and how to repeat it — every row tied to a sentence in the paper.</p>;
  }
  return (
    <div className="mb-4">
      {body}
      {buildError && <p className="mt-2 text-amber-800" role="alert">{buildError}</p>}
      {canBuild && (
        <Button size="sm" className="mt-2 min-h-11" disabled={wait > 0} onClick={onBuild}>
          {wait > 0 ? `Try again in ${wait} s` : data.state === 'failed' ? 'Try again' : 'Build card'}
        </Button>
      )}
    </div>
  );
}

export default function MethodCard({ itemKey }) {
  const queryClient = useQueryClient();
  const key = ['method-card', itemKey];
  const query = useQuery({
    queryKey: key, queryFn: () => fetchMethodCard(itemKey), enabled: Boolean(itemKey),
    staleTime: 30_000, retry: false, refetchOnWindowFocus: false,
    refetchInterval: (q) => !q.state.error && q.state.data?.state === 'building' ? 3000 : false,
  });
  const build = useMutation({ mutationFn: () => buildMethodCard(itemKey), onSuccess: (data) => queryClient.setQueryData(key, data) });
  const data = query.data;
  // Nothing to say until the server answers, and no card for a paper that has no review to read from.
  if (!data || data.reason === 'no_review') return null;
  return <MethodCardView data={data} onBuild={() => build.mutate()} building={build.isPending}
    buildError={build.isError ? 'Could not start the build. Check the connection and try again.' : ''} />;
}
