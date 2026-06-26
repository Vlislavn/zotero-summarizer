// Empty-state card mounted atop /today and /library. Two variants, both reusing
// the existing teal/amber card vocabulary:
//   - "setup"   (app not configured)         → "Finish setup" → /setup
//   - "no-feeds" (Zotero connected, 0 feeds) → "No RSS feeds detected" how-to
//
// Additive: a single import + one line on each host page. Renders nothing when
// neither condition applies, so it never gets in a configured user's way.

import { useNavigate } from 'react-router-dom';
import { useSetupStatus } from '../../hooks/useSetupStatus.js';
import { isSetupDismissed } from './SetupGate.jsx';

export default function NotConfiguredCard() {
  const navigate = useNavigate();
  const { status, isConfigured, isLoading, isError } = useSetupStatus();

  if (isLoading || isError) return null;

  // Not configured → setup nudge (still shown even if the redirect was skipped,
  // since the host page is a fine place to resume from).
  if (!isConfigured) {
    return (
      <div className="mb-4 flex items-start gap-3 p-3 rounded-xl border border-teal-200 bg-teal-50 text-sm text-teal-900">
        <span aria-hidden className="text-base leading-none">⚙️</span>
        <div className="flex-1 leading-snug">
          <p className="font-semibold">Finish setting up Zotero Summarizer</p>
          <p className="text-xs text-teal-800 mt-0.5">
            Connect Zotero, add an LLM, and describe your research so the feed can
            be scored against your goals.
          </p>
        </div>
        <button
          type="button"
          onClick={() => navigate('/setup')}
          className="shrink-0 px-3 py-1.5 rounded-lg bg-teal-600 text-white text-xs font-semibold hover:bg-teal-700"
        >
          Finish setup
        </button>
        {isSetupDismissed() && (
          <span className="sr-only">setup previously skipped</span>
        )}
      </div>
    );
  }

  // Configured + Zotero found but no RSS feeds → how-to nudge.
  const zotero = status?.zotero || {};
  if (zotero.db_found && (zotero.feed_count ?? 0) === 0) {
    return (
      <div className="mb-4 flex items-start gap-3 p-3 rounded-xl border border-amber-200 bg-amber-50 text-sm text-amber-900">
        <span aria-hidden className="text-base leading-none">📡</span>
        <div className="flex-1 leading-snug">
          <p className="font-semibold">No RSS feeds detected in Zotero</p>
          <p className="text-xs text-amber-800 mt-0.5">
            Add feeds in Zotero (File → New Feed, or import an OPML of arXiv /
            journal feeds). Once feeds carry items, they flow into Today for
            culling.
          </p>
        </div>
      </div>
    );
  }

  return null;
}
