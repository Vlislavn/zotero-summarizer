export default function ReviewFailure({ status, label = 'Review failed' }) {
  if (status.status !== 'error' || !status.error) return null;
  const diagnostic = status.diagnostic;
  return (
    <div className="text-[12px] text-rose-700" role="alert">
      {diagnostic ? <>
        <div>{diagnostic.recovery}</div>
        <details className="mt-1">
          <summary className="cursor-pointer">Technical details</summary>
          <div>{diagnostic.stage} / {diagnostic.code}</div>
          <div>{status.error}</div>
          {status.attempt && <pre className="mt-1 whitespace-pre-wrap break-all text-[11px]">{JSON.stringify(status.attempt, null, 2)}</pre>}
        </details>
      </> : <div>{label}: {status.error}</div>}
    </div>
  );
}
