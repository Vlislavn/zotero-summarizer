import { useNavigate } from 'react-router-dom';
import Button from '../ui/Button.jsx';
import { Banner } from '../form/Fields.jsx';
import { dismissSetup } from './SetupGate.jsx';
import { DoctorChecklist } from '../settings/DeploymentCard.jsx';

export default function StepDone({ pathsChanged = false }) {
  const navigate = useNavigate();
  function openToday() {
    dismissSetup();
    navigate('/today');
  }

  return (
    <div className="space-y-5 py-4">
      <div className="text-center">
        <h3 className="text-lg font-bold text-slate-900">Setup saved</h3>
        <p className="text-sm text-slate-500 mt-1">
          You can start now. Verification is optional and may take a few minutes.
        </p>
      </div>

      {pathsChanged && (
        <div className="max-w-sm mx-auto text-left">
          <Banner kind="success">
            You changed the Zotero paths — restart the app to apply them.
          </Banner>
        </div>
      )}

      <div className="text-center"><Button onClick={openToday}>Open Today</Button></div>

      <div className="border-t border-slate-200 pt-4 space-y-1">
        <h4 className="text-sm font-semibold text-slate-800">Optional verification</h4>
        <p className="text-xs text-slate-500">
          Runs real model and no-write pipeline checks. You can retry individual failures.
        </p>
        <DoctorChecklist />
      </div>
    </div>
  );
}
