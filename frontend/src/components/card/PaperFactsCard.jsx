// The paper card on Today + the paper page (Todo #98, redesigned #124). Reads the
// card service (GET /api/card/{key}): facts always, the AI TL;DR when generated +
// consented. Order: AI TL;DR bullets → icons of the facts that were found (#231) →
// who-is-behind line; the legend of every checked fact sits behind ONE disclosure (CardMore).
//
// Additive + fail-soft: a load error renders nothing (never blocks the paper).

import useCard from './useCard.js';
import AiSummary from './AiSummary.jsx';
import { FactLegend, FactStrip, WhoLine } from './FactStrip.jsx';
import Icon from '../ui/Icon.jsx';
import { authorsView, codeView, methodFacts, publishedFacts } from './cardModel.js';

// Shared view of one card's facts (icons, code, who) — one cache entry via useCard.
function useFacts(cardKey) {
  const card = useCard(cardKey).data;
  const facts = card?.facts || {};
  // No full text means the published/method icons never scanned the body: show
  // «unknown» rather than an absence the card cannot prove.
  const noFullText = facts.fulltext?.has_pdf === false;
  return {
    card,
    published: publishedFacts(facts.published, noFullText),
    method: methodFacts(facts.method_checklist, noFullText),
    code: codeView(facts.code),
    authors: authorsView(facts.authors),
  };
}

// The part tucked behind a disclosure: the icon legend. Today renders it inside its
// single “more”; the paper page inside the card's “more”.
export function CardMore({ cardKey }) {
  const { card, published, method, code } = useFacts(cardKey);
  if (!card) return null;
  return <FactLegend published={published} method={method} code={code} />;
}

// The paper page sets reading-size type on every p/li in its main column; the card
// keeps its compact scale there (unboxed: the verdict card above is the only box).
const PAPER_SCALE = '[&_li]:!text-[13px] [&_li]:!leading-snug [&_p]:!text-[12px] [&_p]:!leading-snug';

export default function PaperFactsCard({ cardKey, variant = 'today' }) {
  const cardQuery = useCard(cardKey);
  const { card, published, method, code, authors } = useFacts(cardKey);
  if (!cardKey) return null;
  if (cardQuery.isLoading) {
    return <div className="mt-2 h-4 w-2/3 animate-pulse rounded bg-slate-100" aria-hidden="true" />;
  }
  if (cardQuery.error || !card) return null;  // additive: never block the paper
  const paper = variant === 'paper';
  return (
    <div className={paper ? `space-y-2.5 ${PAPER_SCALE}` : 'mt-3 space-y-2.5'}>
      <AiSummary cardKey={cardKey} card={card} />
      <FactStrip published={published} method={method} code={code} />
      <WhoLine authors={authors} />
      {paper && (
        <details className="group">
          <summary className="flex min-h-8 cursor-pointer list-none items-center gap-1 rounded text-[12px] text-slate-600 hover:text-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-forest-600 [&::-webkit-details-marker]:hidden">
            <Icon name="chevron" size={14} className="transition-transform duration-150 group-open:rotate-90" />more
          </summary>
          <div className="mt-2"><CardMore cardKey={cardKey} /></div>
        </details>
      )}
    </div>
  );
}
