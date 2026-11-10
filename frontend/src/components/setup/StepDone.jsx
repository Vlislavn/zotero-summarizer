import { Link, useLocation, useNavigate } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { doctorQueryOptions } from '../../utils/setupDoctorState.js';
import { fetchDoctorStatus } from '../../api/setupApi.js';
import Button from '../ui/Button.jsx';
import { Banner } from '../form/Fields.jsx';
import { dismissSetup } from './SetupGate.jsx';
import { DoctorChecklist } from '../settings/DeploymentCard.jsx';
import SettingsTaskLink from '../settings/SettingsTaskLink.jsx';

export default function StepDone({ pathsChanged = false }) {
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  const doctor = useQuery(doctorQueryOptions(queryClient, fetchDoctorStatus));
  function openToday() {
    dismissSetup();
    navigate('/today');
  }

  return (
    <div className="space-y-5 py-4">
      <div className="text-center">
        <h3 className="text-lg font-bold text-slate-900">Setup saved</h3>
        <p className="text-sm text-slate-600 mt-1">
          {doctor.data?.ready && !doctor.isFetching && !doctor.isError ? 'Setup verified. Your pipeline is ready.'
            : 'Run verification to finish setup. No model is downloaded automatically.'}
        </p>
      </div>

      {pathsChanged && !doctor.data?.ready && (
        <div className="max-w-sm mx-auto text-left">
          <Banner kind="success">
            You changed the Zotero paths — restart the app to apply them.
          </Banner>
        </div>
      )}

      <div className="text-center space-y-2"><Button onClick={openToday} disabled={!doctor.data?.ready || doctor.isFetching || doctor.isError}
        title={!doctor.data?.ready ? 'Verify setup before opening Today' : undefined}>Open Today</Button>
        {!doctor.data?.ready && <p className="text-xs text-slate-600">
          Need to change inputs? Edit <SettingsTaskLink task="zotero" className="text-teal-700 underline">Zotero paths</SettingsTaskLink> or{' '}
          <Link to="/today?task=rss" state={{ returnTo: location.pathname + location.search + location.hash }}
            className="text-teal-700 underline">RSS sources</Link>, then return to verify.
        </p>}
      </div>

      <div className="border-t border-slate-200 pt-4 space-y-1">
        <h4 className="text-sm font-semibold text-slate-800">Verify the pipeline</h4>
        <p className="text-xs text-slate-600">
          Runs real model and no-write pipeline checks. You can retry individual failures.
        </p>
        <DoctorChecklist />
      </div>
    </div>
  );
}
