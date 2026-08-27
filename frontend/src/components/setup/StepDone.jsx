// Wizard step 4 — Done. Confirms the config was saved and routes to /today,
// where the first daily slate lands. The per-pillar checklist was removed —
// StepProgress already credited the wizard steps throughout the flow, so
// re-rendering the same state in a third idiom only diluted the one next action.

import { useNavigate } from 'react-router-dom';
import Button from '../ui/Button.jsx';
import { Banner } from '../form/Fields.jsx';
import { DoctorChecklist } from '../settings/DeploymentCard.jsx';

export default function StepDone({ pathsChanged = false }) {
  const navigate = useNavigate();
  return (
    <div className="space-y-5 py-4">
      <div className="text-center">
        <h3 className="text-lg font-bold text-slate-900">Verify your setup</h3>
        <p className="text-sm text-slate-500 mt-1">Saved. Ready requires the real pipeline checks below.</p>
      </div>

      {/* The one genuine cross-step reminder: a path change only applies on restart,
          and that fact was set two steps ago — carry it here so it isn't lost. */}
      {pathsChanged && (
        <div className="max-w-sm mx-auto text-left">
          <Banner kind="success">
            You changed the Zotero paths — restart the app to apply them.
          </Banner>
        </div>
      )}

      <DoctorChecklist autoRun completion={
        <div className="text-center"><Button onClick={() => navigate('/today')}>Open Today</Button></div>
      } />
    </div>
  );
}
