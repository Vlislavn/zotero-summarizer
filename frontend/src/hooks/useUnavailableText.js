import { useEffect, useState } from 'react';
import { isServerAvailable, syncStatusEvent, transportUnavailable } from '../offlineStore.js';

// The one wording rule for a server-only control that is not available: "The app is busy" when the
// app answered but errored/was slow; the "needs the Mac" text only when it truly cannot be reached;
// "Checking the connection…" before the first answer. The state is kept here (not read at render)
// so a mounted label follows the connection when it goes from unknown to unreachable.
const connectionKind = () => (isServerAvailable({ allowSyncFailure: true }) ? 'busy'
  : (!navigator.onLine || transportUnavailable()) ? 'mac' : 'checking');

export default function useUnavailableText() {
  const [kind, setKind] = useState(connectionKind);
  useEffect(() => {
    const update = () => setKind(connectionKind());
    const events = ['offline', 'online', syncStatusEvent];
    events.forEach((name) => window.addEventListener(name, update));
    return () => events.forEach((name) => window.removeEventListener(name, update));
  }, []);
  const say = (macText) => (kind === 'busy' ? 'The app is busy — try again.'
    : kind === 'mac' ? macText : 'Checking the connection…');
  return { busy: kind === 'busy', say };
}
