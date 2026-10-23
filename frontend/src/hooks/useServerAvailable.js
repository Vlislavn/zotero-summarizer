import { useEffect, useState } from 'react';
import { isServerAvailable, syncStatusEvent } from '../offlineStore.js';

export default function useServerAvailable() {
  const [available, setAvailable] = useState(isServerAvailable);
  useEffect(() => {
    const update = () => setAvailable(isServerAvailable());
    window.addEventListener('offline', update);
    window.addEventListener('online', update);
    window.addEventListener(syncStatusEvent, update);
    return () => {
      window.removeEventListener('offline', update);
      window.removeEventListener('online', update);
      window.removeEventListener(syncStatusEvent, update);
    };
  }, []);
  return available;
}
