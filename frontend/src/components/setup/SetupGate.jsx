// First-run gate. Wraps the app's <Routes>. Redirects to /setup ONLY from the
// default landing surface (`/` or `/library`) when the app is not configured
// AND the user hasn't dismissed setup. Never traps a returning user: any other
// route renders untouched, and "Skip for now" (in the wizard) persists
// zs:setupDismissed=1 so the redirect never fires again.

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

  // Only ever redirect from the default landing surface. Don't flash a redirect
  // while the first status query is in flight, and fail open on a status error
  // (a broken /api/setup/status must never block the whole app).
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
