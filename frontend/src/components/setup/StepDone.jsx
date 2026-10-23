import { Link, useNavigate } from 'react-router-dom';
import { useIsMutating, useQuery, useQueryClient } from '@tanstack/react-query';
import { fetchDoctorStatus } from '../../api/setupApi.js';
import { doctorQueryOptions } from '../../utils/setupDoctorState.js';
import Button from '../ui/Button.jsx';
import { Banner } from '../form/Fields.jsx';
import { dismissSetup } from './SetupGate.jsx';
import { DoctorChecklist } from '../settings/DeploymentCard.jsx';

export default function StepDone({ pathsChanged = false }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const doctor = useQuery(doctorQueryOptions(queryClient, fetchDoctorStatus));
  const checking = useIsMutating({ mutationKey: ['setup-doctor-run'] }) > 0 || doctor.data?.status === 'running';
  const verified = Boolean(doctor.data?.ready && !doctor.isFetching && !doctor.isError && !checking);
  function openToday() {
    dismissSetup();
    navigate('/today');
  }

  return (
    <div className="space-y-5 py-4">
      <div className="text-center">
        <h3 className="text-lg font-medium text-slate-900">Setup saved</h3>
        <p className="text-sm text-slate-500 mt-1">
          {verified ? 'Setup verified. Your pipeline is ready.'
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

      <div className="text-center space-y-2"><Button onClick={openToday} disabled={!verified}
        title={!doctor.data?.ready ? 'Verify setup before opening Today' : undefined}>Open Today</Button>
        {!doctor.data?.ready && <p className="text-xs text-slate-600">
          Missing Zotero or RSS source? <Link to="/settings" className="text-teal-700 underline">Configure it in Settings</Link>,
          then return to verify.
        </p>}
      </div>

      <div className="border-t border-slate-200 pt-4 space-y-1">
        <h4 className="text-sm font-semibold text-slate-800">Verify the pipeline</h4>
        <p className="text-xs text-slate-500">
          Runs real model and no-write pipeline checks. You can retry individual failures.
        </p>
        <DoctorChecklist expanded onOpenAiSettings={() => navigate('/settings#ai-models')}
          onOpenSettings={(target) => navigate(`/settings#${target}`)} />
      </div>
    </div>
  );
}
