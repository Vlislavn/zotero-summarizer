import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { fetchReviewAudit } from '../../../api/libraryApi.js';
import { Fold, SectionLabel } from './primitives.jsx';

// "Evidence": the verbatim quotes that backed each verified claim of the saved
// review (read-only export, #40). Quiet by default (one fold line); states are
// said in plain words — never an error for "no evidence", never a guess.
const FIELDS = [['tldr', 'Contribution'], ['key_findings', 'Findings']];
const LOST_LABEL = {
  methods: 'Methods', limitations: 'Limitations', key_weakness: 'Weakness', key_strength: 'Strength',
  executive_summary: 'Summary', impact: 'Impact', controversies: 'Controversies',
  tldr: 'Contribution', key_findings: 'Findings', unknown_unknowns: 'Unknowns',
};
// [closed-row line (fits one iPhone line), full sentence inside the fold]
const STATE_TEXT = {
  legacy_audit_unavailable: ['Quotes not saved — re-run the review', 'This review was saved before supporting quotes were kept. Re-run the review to get them.'],
  source_unavailable: ['PDF not available', 'The paper’s PDF is not available, so its quotes cannot be shown.'],
  source_bytes_changed: ['PDF changed — re-run the review', 'The PDF changed after this review, so the saved quotes are hidden. Re-run the review.'],
  audit_contract_changed: ['Review checks changed — re-run the review', 'The review checks changed and the saved quotes cannot be reused. Re-run the review.'],
};
const CHANGED_TEXT = ['Review changed — re-run the review', 'The review changed after its claims were checked, so the saved quotes are hidden. Re-run the review.'];
const UNAVAILABLE_TEXT = ['Quotes not available', 'Supporting quotes are not available for this review.'];

const WORDS = (text) => new Set((String(text).toLowerCase().match(/[a-z0-9][a-z0-9.%-]{3,}/g) || []));
// Front matter and licence lines ("CC-BY 4.0 International licence", "bioRxiv preprint doi…", e-mails)
// are never the sentence to show for a finding.
const BOILERPLATE = /@|\bCC[- ]BY\b|creative commons|\ball rights reserved\b|\bcopyright\b|\(c\)|©|made available under|\bpreprint\b.*\bdoi\b|\bdoi\.org\b|\bhas not been certified by peer review\b|\bthe copyright holder\b|\bbiorxiv\b|\bmedrxiv\b/i;
const SENTENCE_BREAK = /(?<=[.!?])\s+(?=[A-Z0-9“"[(])/;

// Presentation only: the stored passage is a ~1200-char verifier chunk. Show the
// sentence best matching the claim (word overlap, front matter / licence lines last)
// ±1 neighbour; the whole passage stays one click away. Verbatim, deterministic.
function bestWindow(passage, claim) {
  const sentences = passage.split(SENTENCE_BREAK);
  const want = WORDS(claim);
  const scores = sentences.map((x) => {
    const words = WORDS(x);
    return [...want].filter((w) => words.has(w)).length - (BOILERPLATE.test(x) ? 100 : 0);
  });
  const best = scores.indexOf(Math.max(...scores));
  const keep = (i) => i === best || !BOILERPLATE.test(sentences[i]);  // never pull in front matter as context
  let from = best;
  let to = best + 1;
  if (best > 0 && keep(best - 1)) from = best - 1;
  if (best + 1 < sentences.length && keep(best + 1)) to = best + 2;
  return { text: sentences.slice(from, to).join(' '), head: from > 0, tail: to < sentences.length };
}

function Quote({ text, claim, excerpt }) {
  const [open, setOpen] = useState(false);
  const win = bestWindow(text, claim);
  const whole = !win.head && !win.tail;
  const shown = open || whole ? text : win.text;
  const lead = open || whole ? excerpt : win.head || excerpt;
  const trail = !(open || whole) && win.tail;
  return (
    <blockquote className="border-l-2 border-sage-300 pl-3 !text-[14px] text-slate-600">
      “{lead ? '…' : ''}{shown}{trail ? '…' : ''}”
      {!whole && (
        <button type="button" onClick={() => setOpen(!open)} aria-expanded={open}
          className="flex min-h-11 items-center !text-[13px] text-forest-800 underline underline-offset-2 hover:text-forest-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-forest-400">
          {open ? 'Show less' : 'Show full passage'}
        </button>
      )}
    </blockquote>
  );
}

/** Pure view-model: { note, groups:[{label, claims:[{claim, quotes}]}] } or { short, message }. */
function evidenceModel(audit) {
  const full = audit?.verification_audit;
  const evidence = full
    ? { audit: full, note: '' }
    : audit?.retained_evidence
      ? { audit: audit.retained_evidence.verification_audit, note: 'Quotes come from the PDF file that was reviewed.' }
      : audit?.older_rules_evidence
        ? { audit: audit.older_rules_evidence.verification_audit, note: 'Checked under an older version of the review rules — the paper is unchanged.' }
        : null;
  if (!evidence) {
    const reason = audit?.reason || '';
    const [short, message] = STATE_TEXT[reason] || (audit?.audit_status === 'stale' ? CHANGED_TEXT : UNAVAILABLE_TEXT);
    return { short, message };
  }
  const passages = new Map((evidence.audit.passages || []).map((p) => [p.id, p]));
  const groups = FIELDS.map(([field, label]) => ({
    label,
    claims: (evidence.audit.claims || []).filter((c) => c.field === field).map((c) => ({
      claim: c.claim,
      quotes: (c.evidence || []).map((id) => passages.get(id)).filter(Boolean)
        .map((p) => ({ id: p.id, text: String(p.text || '').trim(), page: p.pdf_page, excerpt: (p.selected_span?.start || 0) > 0 })).filter((q) => q.text),
    })),
  })).filter((g) => g.claims.length);
  return { note: evidence.note, groups };
}

export default function EvidenceFold({ itemKey, lost = [] }) {
  const query = useQuery({
    queryKey: ['review-audit', itemKey],
    queryFn: () => fetchReviewAudit(itemKey),
    enabled: Boolean(itemKey), staleTime: 60_000, retry: false,
  });
  const model = query.isError
    ? { short: UNAVAILABLE_TEXT[0], message: UNAVAILABLE_TEXT[1] }
    : query.data ? evidenceModel(query.data) : null;
  const count = model?.groups?.reduce((n, g) => n + g.claims.length, 0) || 0;
  const summary = !model ? 'Loading…' : model.short || `${count} verified ${count === 1 ? 'claim' : 'claims'} with quotes from the paper`;
  const ungrounded = lost.map((f) => LOST_LABEL[f] || f);
  return (
    <Fold label="Evidence" summary={summary}>
      <div className="space-y-4">
        {model?.message && <p className="text-slate-500">{model.message}</p>}
        {model?.note && <p className="!text-[13px] text-slate-500">{model.note}</p>}
        {model?.groups?.map((group) => (
          <section key={group.label} className="space-y-3">
            <SectionLabel level={3}>{group.label}</SectionLabel>
            {group.claims.map((c, i) => (
              <div key={i} className="space-y-1.5">
                <p className="!text-[14px] text-slate-800">{c.claim}</p>
                {c.quotes.map((q) => <Quote key={q.id} text={q.text} claim={c.claim} excerpt={q.excerpt} />)}
              </div>
            ))}
          </section>
        ))}
        {ungrounded.length > 0 && (
          <section>
            <SectionLabel level={3}>Not grounded</SectionLabel>
            <p className="mt-1 !text-[14px] text-slate-600">
              {ungrounded.join(', ')} — could not be grounded this time.
            </p>
          </section>
        )}
      </div>
    </Fold>
  );
}
