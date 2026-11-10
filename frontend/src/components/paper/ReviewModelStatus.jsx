import SettingsTaskLink from '../settings/SettingsTaskLink.jsx';

// Model-check state is not paper quality. A consent refusal is not an outage;
// retry reads connection metadata only and never starts generation.
export default function ReviewModelStatus({ runner }) {
  const { online, llm, llmAvailable, checkingModel, modelCheckPending, modelCheckError,
    retryModelCheck } = runner;
  if (!online) return <div role="status" className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-[13px] text-amber-800">
    Offline — cached reviews remain readable. Reconnect to generate a review.
  </div>;
  if (llmAvailable) return null;

  const disabled = !modelCheckError && llm?.enabled === false;
  const approval = !modelCheckError && llm?.reason_code === 'provider_approval_required';
  const changed = !modelCheckError && ['connection_conflict', 'configuration_busy'].includes(llm?.reason_code);
  const pending = modelCheckPending || (checkingModel && !llm);
  const message = pending ? 'Checking the review model connection…'
    : disabled ? 'AI reviews are off.'
      : approval ? 'The saved model connection needs approval before it can be checked. This is not a network error.'
        : changed ? 'Model settings changed or are being saved. Retry the connection check.'
          : modelCheckError ? 'Could not read the model connection status. Retry the check.'
            : llm?.reachable === false ? 'The review model connection is unavailable. Check its saved settings or retry.'
              : 'The review model connection has not been confirmed. Retry the check.';
  return <div role="status" className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-[13px] text-amber-800">
    <p>{message}</p>
    {!pending && <div className="mt-1 flex flex-wrap items-center gap-x-3">
      {!changed && <SettingsTaskLink task="connection" className="min-h-11 inline-flex items-center font-semibold underline">
        {approval ? 'Approve saved connection' : 'Open AI settings'}
      </SettingsTaskLink>}
      {!disabled && <button type="button" onClick={retryModelCheck} disabled={checkingModel}
        className="min-h-11 font-semibold underline disabled:opacity-50">Retry model check</button>}
    </div>}
  </div>;
}
