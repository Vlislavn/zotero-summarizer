// The card's "what is in this paper" icon row (owner: small, beautiful icons,
// not a wall of text). The row shows ONLY the facts that were found (#231); with none
// it is one quiet line. The legend in the card's “more” keeps every checked fact in
// its three honest states — present: Forest icon on Mint; absent: faint outline;
// unknown: dashed circle (no full text read, absence unproven) — with the words, so
// touch users, who get no tooltip, can learn the icons. Every icon carries a plain
// English aria-label + title.

import Icon from '../ui/Icon.jsx';

const SHELL = {
  present: 'bg-sage-100 text-forest-800',
  absent: 'text-slate-500 opacity-45',
  unknown: 'border border-dashed border-slate-300 text-slate-500 opacity-70',
};
const DOT = { ok: ['bg-forest-800', 'check'], bad: ['bg-rose-500', 'x'] };

export function FactIcon({ icon, state, label, dot = null, compact = false }) {
  const box = compact ? 'h-6 w-6' : 'h-8 w-8 sm:h-7 sm:w-7';
  return (
    <span role="img" aria-label={label} title={label}
      className={`relative inline-flex shrink-0 items-center justify-center rounded-full ${box} ${SHELL[state]}`}>
      <Icon name={icon} size={compact ? 14 : 16} />
      {dot && <span aria-hidden="true"
        className={`absolute -bottom-0.5 -right-0.5 flex h-4 w-4 items-center justify-center rounded-full border-[1.5px] border-white text-white ${DOT[dot][0]}`}>
        <Icon name={DOT[dot][1]} size={8} strokeWidth={3.5} />
      </span>}
    </span>
  );
}

function CodeIcon({ code, compact }) {
  const icon = <FactIcon icon="github" state={code.state} label={code.label} dot={code.dot} compact={compact} />;
  return code.url
    ? <a href={code.url} target="_blank" rel="noreferrer noopener" className="-m-1.5 inline-flex p-1.5 hover:opacity-80">{icon}</a>
    : icon;
}

const isFound = (fact) => fact.state === 'present';

// Found facts only (published, method, then the code mark), one compact row.
export function FactStrip({ published, method, code }) {
  const found = [...published, ...method].filter(isFound);
  if (!found.length && !isFound(code)) {
    // Silence would read as “checked, nothing there”, so say which of the two it is.
    const unread = [...published, ...method, code].some((f) => f.state === 'unknown');
    return (
      <p className="text-[12px] text-slate-500">
        Code, data, method: {unread ? 'full text not read' : 'nothing found'}
      </p>
    );
  }
  return (
    <div role="group" aria-label="What the paper has" className="flex flex-wrap items-center gap-1">
      {found.map((f) => <FactIcon key={f.id} {...f} />)}
      {isFound(code) && <CodeIcon code={code} />}
    </div>
  );
}

const LEGEND_LABEL = 'text-[11px] font-medium uppercase tracking-[0.06em] text-slate-500';

// Legend for “more”: the same icons with their words, grouped; plus the state key.
export function FactLegend({ published, method, code }) {
  const group = (title, facts) => (
    <div>
      <p className={LEGEND_LABEL}>{title}</p>
      <ul className="mt-1 space-y-1">
        {facts.map((f) => (
          <li key={f.id} className="flex items-center gap-2 text-[12px] leading-snug text-slate-600">
            <FactIcon {...f} compact />{f.label}
          </li>
        ))}
      </ul>
    </div>
  );
  return (
    <div className="space-y-2.5">
      <div className="grid gap-x-6 gap-y-2.5 sm:grid-cols-2">
        {group('Published', published)}
        {group('Method', method)}
      </div>
      <div>
        <p className={LEGEND_LABEL}>Code</p>
        <p className="mt-1 flex items-center gap-2 text-[12px] leading-snug text-slate-600">
          <CodeIcon code={code} compact />{code.label}
        </p>
      </div>
      <p className="text-[11px] leading-snug text-slate-500">
        Green circle: present · faint icon: not found · dashed: unknown (full text not read).
      </p>
    </div>
  );
}

// Who is behind the paper: ONE quiet line; confidence is a dot, not a sentence.
export function WhoLine({ authors }) {
  const parts = [...authors.affiliations, authors.hIndex != null ? `h-index ${authors.hIndex}` : null].filter(Boolean);
  if (!parts.length) return null;
  const confidence = `Confidence: ${authors.confidence}`;
  return (
    <p className="flex min-w-0 items-center gap-1.5 text-[12px] text-slate-500" title={`Who is behind it: ${parts.join(' · ')}`}>
      <Icon name="building" size={14} className="text-slate-400" />
      <span className="truncate">{parts.join(' · ')}</span>
      <span role="img" aria-label={confidence} title={confidence}
        className={`h-1.5 w-1.5 shrink-0 rounded-full ${authors.confident ? 'bg-forest-600' : 'border border-slate-400'}`} />
    </p>
  );
}
