import { Navigate, useLocation } from 'react-router-dom';
import { useSetupStatus } from '../../hooks/useSetupStatus.js';

export const SETUP_DISMISSED_KEY = 'zs:setupDismissed';

export function isSetupDismissed() {
  try {
    return window.localStorage.getItem(SETUP_DISMISSED_KEY) === '1';
  } catch {
    return false;
  }
}

export function dismissSetup() {
  try {
    window.localStorage.setItem(SETUP_DISMISSED_KEY, '1');
  } catch {
    /* no-op: incognito / disabled storage */
  }
}

const LANDING_PATHS = new Set(['/', '/library']);

export default function SetupGate({ children }) {
  const { pathname } = useLocation();
  const { isConfigured, isLoading, isError } = useSetupStatus();

  const onLanding = LANDING_PATHS.has(pathname);
  if (
    onLanding &&
    !isLoading &&
    !isError &&
    !isConfigured &&
    !isSetupDismissed()
  ) {
    return <Navigate to="/setup" replace />;
  }

  return children;
}
