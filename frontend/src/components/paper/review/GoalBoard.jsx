import { Disclosure, KeyVal, SectionAnchor } from './primitives.jsx';
import { shortGoal, isSupportedGoal } from './briefModel.js';

const STATE_LABEL = {
  hit: '● addressed', miss: '○ not addressed', not_retrieved: '⚠ not retrieved',
};

// 1-3 col grid of goal tiles — the single home of per-goal relevance. Tiles are
// peers in a grid (the one sanctioned use of light framing), keyed by state with
// a left accent; a fired tile carries its grounded summary, sections to read,
// and the supporting quote on demand.
const TILE_STATE = {
  hit: 'border-l-emerald-500',
  miss: 'border-l-slate-300 opacity-80',
  not_retrieved: 'border-l-amber-400 opacity-75',
};

function goalWhy(state, g, supported) {
  if (state === 'hit') return g?.abstained || !String(g?.summary || '').trim() ? 'grounded summary withheld'
    : supported ? 'Relevant to this goal' : 'Evidence did not support this goal';
  if (state === 'miss') return 'not addressed in this paper';
  return 'retrieval degraded — not assessed';
}

function goalStateLabel(state, g, supported) {
  if (state === 'hit' && g?.abstained) return '○ abstained';
  if (state === 'hit' && !supported) return '○ not supported';
  return STATE_LABEL[state] || state;
}

function GoalMeter({ goal, score }) {
  const width = Math.round(Math.max(0, Math.min(1, score / 3)) * 100);
  return (
    <div className="my-1.5 h-1 rounded-full bg-slate-200/80 overflow-hidden" role="meter"
      aria-label={`${shortGoal(goal)} relevance`} aria-valuemin={0} aria-valuemax={3}
      aria-valuenow={Math.max(0, Math.min(3, score))}>
      <span className="block h-full bg-teal-500" style={{ width: `${width}%` }} />
    </div>
  );
}

// Where a fired goal was found: the located sections, else the sections the model read.
function GoalWhere({ sections, secs }) {
  if (sections?.length) {
    return (
      <div className="mt-1.5 flex flex-wrap items-center gap-1">
        <span className="text-[11px] text-slate-400">In</span>
        {sections.map((s) => <SectionAnchor key={s.id} section={s} />)}
      </div>
    );
  }
  return secs ? <div className="mt-1.5 text-[11px] text-slate-400">Read for you: {secs}</div> : null;
}

function GoalEvidence({ why, foldReason, quotes }) {
  return (
    <details className="mt-1 group">
      <summary className="cursor-pointer list-none [&::-webkit-details-marker]:hidden rounded text-[11px] font-medium text-teal-600 hover:text-teal-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-400 focus-visible:ring-offset-1">
        evidence
      </summary>
      {foldReason && <div className="text-[12px] leading-relaxed text-slate-600">{why}</div>}
      {quotes.map((quote, i) => <blockquote key={`${i}-${quote}`}
        className="mt-1 border-l-2 border-teal-300 pl-2 text-[11px] italic text-slate-500 leading-relaxed">
        “{quote}”
      </blockquote>)}
    </details>
  );
}

function goalTone(state, supported) {
  return state === 'hit' && !supported ? TILE_STATE.miss : TILE_STATE[state] || TILE_STATE.not_retrieved;
}

// What a tile shows, derived from one goal summary.
function goalFacts(g) {
  const state = String(g?.retrieval_state || 'not_retrieved');
  const supported = isSupportedGoal(g);
  return {
    state, supported,
    score: Number(g?.score) || 0,
    secs: (g?.key_sections || []).filter(Boolean).join(', '),
    quotes: (g?.supporting_quotes || []).map((q) => String(q || '').trim()).filter(Boolean),
  };
}

function GoalTile({ g, sections, foldReason }) {
  const { state, supported, score, secs, quotes } = goalFacts(g);
  const hit = state === 'hit';
  const why = goalWhy(state, g, supported);
  return (
    <div className={`border-l-2 py-0.5 pl-3 ${goalTone(state, supported)}`}>
      <div className="text-[12px] font-medium text-slate-800 leading-snug">{g?.goal}</div>
      <div className="mt-0.5 text-[11px] text-slate-400">{goalStateLabel(state, g, supported)}</div>
      <GoalMeter goal={g?.goal} score={score} />
      {!foldReason && <div className="text-[12px] leading-relaxed text-slate-600">{why}</div>}
      {hit && <GoalWhere sections={sections} secs={secs} />}
      {(foldReason || (hit && quotes.length > 0)) && <GoalEvidence why={why} foldReason={foldReason} quotes={quotes} />}
    </div>
  );
}

function uniqueGoalFindings(goals) {
  const findings = new Map();
  for (const goal of goals) {
    if (goal?.retrieval_state !== 'hit') continue;
    for (const sentence of String(goal.summary || '').trim().split(/(?<=[.!?])\s+/)) {
      const text = sentence.trim();
      let key = text.toLocaleLowerCase().replace(/\s+/g, ' ').trim();
      const symbols = text.match(/\b[A-Za-z]{1,5}(?:\d+|\s*=\s*\d[\d.,]*)/g) || [];
      if (text !== text.toLocaleUpperCase()) symbols.push(...(text.match(/\b[A-Z]{2,}\b/g) || []));
      if (symbols.length) key += `|${symbols.map((s) => s.replace(/\s+/g, '')).join('|')}`;
      if (!key) continue;
      if (!findings.has(key)) findings.set(key, { text, goals: [] });
      const labels = findings.get(key).goals;
      if (goal.goal && !labels.includes(goal.goal)) labels.push(goal.goal);
    }
  }
  return [...findings.values()];
}

function GoalFindings({ goals }) {
  const findings = uniqueGoalFindings(goals);
  if (!findings.length) return null;
  const items = (selected) => {
    const groups = new Map();
    for (const finding of selected) {
      const key = JSON.stringify(finding.goals);
      if (!groups.has(key)) groups.set(key, { goals: finding.goals, findings: [] });
      groups.get(key).findings.push(finding);
    }
    return [...groups.entries()].map(([key, group]) => <div key={key} className="mb-2 break-words">
      <strong className="font-medium text-slate-700">For: {group.goals.join('; ')}</strong>
      <ul className="list-disc pl-5">{group.findings.map(finding => {
        const words = finding.text.split(/\s+/);
        return <li key={finding.text} className="mb-2">
          <p className="text-slate-600">{words.slice(0, 30).join(' ')}{words.length > 30 ? '…' : ''}</p>
          {words.length > 30 && <details><summary className="cursor-pointer text-teal-700 focus-visible:ring-2">Full finding</summary>
            <p>{finding.text}</p></details>}
        </li>;
      })}</ul>
    </div>);
  };
  return <div className="text-xs">{items(findings.slice(0, 3))}
    {findings.length > 3 && <details><summary className="cursor-pointer text-teal-700 focus-visible:ring-2">
      {findings.length - 3} more findings</summary>{items(findings.slice(3))}</details>}
  </div>;
}

// Show located evidence first, including evidence against relevance. Retrieval
// misses and degraded goals stay behind one disclosure; if none are located,
// show them all so the section isn't empty.
export function GoalBoard({ goals, goalLoc, foldReasons = false }) {
  const isHit = (g) => String(g?.retrieval_state || 'not_retrieved') === 'hit';
  const addressed = goals.filter(isHit);
  const rest = goals.filter((g) => !isHit(g));
  const findingTexts = new Set(uniqueGoalFindings(addressed).map(f => f.text));
  const originals = [...new Set(goals.map(g => String(g.summary || '').trim()))]
    .filter(text => text && !findingTexts.has(text));
  const grid = (items) => (
    <div className="goal-board-grid grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-x-5 gap-y-4">
      {items.map((g, i) => <GoalTile key={i} g={g} sections={goalLoc?.get(g?.goal)} foldReason={foldReasons} />)}
    </div>
  );
  return (
    <div className="space-y-2">
      {grid(addressed.length ? addressed : goals)}
      <Disclosure summary="Goal findings"><GoalFindings goals={addressed} /></Disclosure>
      {originals.length > 0 && <Disclosure summary="Original goal summaries">
        <dl>{originals.map((text, i) => <KeyVal key={i}
          label={goals.filter(g => String(g.summary || '').trim() === text).map(g => g.goal).join('; ')}>{text}</KeyVal>)}</dl>
      </Disclosure>}
      {addressed.length > 0 && rest.length > 0 && (
        <details className="group">
          <summary className="cursor-pointer list-none [&::-webkit-details-marker]:hidden text-[11px] font-medium text-slate-400 hover:text-slate-600">
            {rest.length} other goal{rest.length > 1 ? 's' : ''} ▾
          </summary>
          <div className="mt-2 opacity-75">{grid(rest)}</div>
        </details>
      )}
    </div>
  );
}
