// Readiness strip for the top of Settings. Four pills — Zotero · LLM · Goals ·
// Classifier — derived from useSetupStatus. A failing pill is actionable: it
// either scrolls to the relevant region (via an anchor id) or routes to the
// wizard / model lifecycle, so "what's broken?" answers itself. ("Classifier"
// names the trained drop-classifier — distinct from the LLM, which is the "LLM"
// pill — so the two model concepts don't collide.)

import { useNavigate } from 'react-router-dom';
import { useSetupStatus } from '../../hooks/useSetupStatus.js';

function Pill({ label, ok, onClick, title }) {
  const base =
    'inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full border text-xs font-semibold transition-colors';
  const tone = ok
    ? 'bg-emerald-50 border-emerald-200 text-emerald-800'
    : 'bg-rose-50 border-rose-200 text-rose-800 hover:bg-rose-100 cursor-pointer';
  return (
    <button
      type="button"
      onClick={ok ? undefined : onClick}
      disabled={ok}
      title={title}
      className={`${base} ${tone} ${ok ? 'cursor-default' : ''}`}
    >
      <span aria-hidden>{ok ? '✓' : '✗'}</span>
      {label}
    </button>
  );
}

// Scroll an Essentials anchor into view (the failing field), falling back to a
// no-op if it isn't mounted.
function scrollToAnchor(id) {
  const el = typeof document !== 'undefined' ? document.getElementById(id) : null;
  if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

// "dep:lightgbm" / "classifier_gate" → a short human label for the pill.
function subsystemLabel(name) {
  if (name.startsWith('dep:')) return name.slice(4);
  return name.replace(/_/g, ' ');
}

export default function ReadinessStrip() {
  const navigate = useNavigate();
  const { pillars, subsystemIssues, isLoading, isError } = useSetupStatus();

  if (isLoading || isError) return null;
  // An all-green strip tells the user nothing actionable (it just restates that
  // setup is done, and the LLM dot already lives in Active models below). Surface
  // it ONLY when something needs fixing — success is silent (Tesler / Occam).
  const allReady = pillars.zotero && pillars.llm && pillars.goals && pillars.model
    && subsystemIssues.length === 0;
  if (allReady) return null;

  return (
    <div className="rounded-xl bg-amber-50/50 p-2.5 flex flex-wrap items-center gap-2">
      <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-500 mr-1">
        Needs attention
      </span>
      <Pill
        label="Zotero"
        ok={pillars.zotero}
        onClick={() => scrollToAnchor('essentials-zotero-paths')}
        title={pillars.zotero ? 'Zotero DB found' : 'Zotero DB not found — set the data directory below'}
      />
      <Pill
        label="LLM"
        ok={pillars.llm}
        onClick={() => scrollToAnchor('ai-models')}
        title={pillars.llm ? 'LLM reachable' : 'LLM not reachable — check the provider in AI models'}
      />
      <Pill
        label="Goals"
        ok={pillars.goals}
        onClick={() => scrollToAnchor('essentials-goals')}
        title={pillars.goals ? 'Research goals set' : 'No research goals — add at least one below'}
      />
      <Pill
        label="Classifier"
        ok={pillars.model}
        onClick={() => navigate('/setup')}
        title={pillars.model ? 'Classifier trained' : 'No trained classifier — retrain below or run setup'}
      />
      {/* Runtime subsystems down right now (missing dep, gate failed to load…) —
          ONE summary pill rather than N identical-destination pills (Hick's Law:
          the answer is "something's down, go here"). Detail in the tooltip. */}
      {subsystemIssues.length > 0 && (
        <Pill
          label={`${subsystemIssues.length} subsystem${subsystemIssues.length === 1 ? '' : 's'} down`}
          ok={false}
          onClick={() => navigate('/setup')}
          title={subsystemIssues.map((s) => s.detail || subsystemLabel(s.name)).join(' · ')}
        />
      )}
    </div>
  );
}
