import { useEffect, useState } from 'react';
import { isServerAvailable, syncStatusEvent } from '../offlineStore.js';

// Browser connectivity alone does not say whether the Mac is running.
export default function useServerAvailable() {
  const [available, setAvailable] = useState(isServerAvailable);
  useEffect(() => {
    const offline = () => setAvailable(false);
    const online = () => setAvailable(isServerAvailable());
    const sync = ({ detail }) => setAvailable(detail.online && !detail.sync_error && detail.connection !== 'unavailable');
    window.addEventListener('offline', offline);
    window.addEventListener('online', online);
    window.addEventListener(syncStatusEvent, sync);
    return () => {
      window.removeEventListener('offline', offline);
      window.removeEventListener('online', online);
      window.removeEventListener(syncStatusEvent, sync);
    };
  }, []);
  return available;
}
