// Wizard step 4 — Done. Confirms the config was saved and routes to /today,
// where the first daily slate lands.

import { useNavigate } from 'react-router-dom';

export default function StepDone({ pillars }) {
  const navigate = useNavigate();
  const rows = [
    ['Zotero', pillars?.zotero],
    ['LLM', pillars?.llm],
    ['Goals', pillars?.goals],
  ];
  return (
    <div className="space-y-5 text-center py-4">
      <div className="text-5xl" aria-hidden>🎉</div>
      <div>
        <h3 className="text-lg font-bold text-slate-900">You&apos;re set</h3>
        <p className="text-sm text-slate-500 mt-1">
          Your configuration is saved. The feed daemon will start scoring papers
          against your goals.
        </p>
      </div>

      <ul className="inline-flex flex-col gap-1.5 text-sm text-left">
        {rows.map(([label, ok]) => (
          <li key={label} className="flex items-center gap-2">
            <span className={ok ? 'text-emerald-600' : 'text-amber-500'} aria-hidden>
              {ok ? '✓' : '○'}
            </span>
            <span className="text-slate-700">{label}</span>
          </li>
        ))}
      </ul>

      <div>
        <button
          type="button"
          onClick={() => navigate('/today')}
          className="px-5 py-2.5 rounded-lg bg-slate-900 text-white text-sm font-semibold hover:bg-slate-700"
        >
          Go to Today
        </button>
      </div>
    </div>
  );
}
