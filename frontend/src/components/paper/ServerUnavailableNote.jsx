import useServerAvailable from '../../hooks/useServerAvailable.js';
import { syncNow } from '../../syncClient.js';

// Shown where a server-only control would be. "Needs the Mac" only when the app truly cannot be
// reached; if it answered with an error (e.g. busy), say so and let the user re-check.
export default function ServerUnavailableNote({ what, children }) {
  const answered = useServerAvailable({ allowSyncFailure: true });
  return (
    <p className="text-xs text-slate-500">
      {answered ? 'The app is busy — try again.' : `${what} need the Mac.`}
      {answered && <> <button type="button" onClick={() => syncNow()} className="font-semibold text-teal-700">Check now</button></>}
      {children}
    </p>
  );
}
