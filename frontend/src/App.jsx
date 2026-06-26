import { Navigate, Route, Routes, useLocation } from 'react-router-dom';
import NavBar from './components/NavBar.jsx';
import Today from './pages/Today.jsx';
import Settings from './pages/Settings.jsx';
import Library from './pages/Library.jsx';
import PaperReviewPage from './pages/PaperReviewPage.jsx';
import Ops from './pages/Ops.jsx';
import Audit from './pages/Audit.jsx';
import SetupFlow from './pages/SetupFlow.jsx';
import SetupGate from './components/setup/SetupGate.jsx';

// Carry a legacy path's query string through to its new home so a bookmarked
// deep link survives the redirect (e.g. /review?state=gate_rejected ->
// /ops?tab=review&state=gate_rejected). `extra` injects the new home's own
// params (the tab/mode the old page folded into); existing params win on a clash.
function RedirectTo({ to, extra }) {
  const { search } = useLocation();
  const params = new URLSearchParams(search);
  for (const [k, v] of Object.entries(extra || {})) {
    if (!params.has(k)) params.set(k, v);
  }
  const qs = params.toString();
  return <Navigate to={qs ? `${to}?${qs}` : to} replace />;
}

export default function App() {
  return (
    <div className="min-h-screen px-4 py-5 max-w-[1400px] mx-auto">
      <NavBar />
      <main>
        {/* SetupGate redirects an unconfigured first-run user from the default
            landing (/ or /library) to /setup — never traps a returning user. */}
        <SetupGate>
          <Routes>
            {/* Land on Read next (the Library reading queue) — the user's most
                frequent entry; saves a Today→Library click every session. */}
            <Route path="/" element={<Navigate to="/library" replace />} />
            <Route path="/setup" element={<SetupFlow />} />
            <Route path="/today" element={<Today />} />
            <Route path="/settings" element={<Settings />} />

            {/* Library: Read next (default) + Batch label (?mode=batch). */}
            <Route path="/library" element={<Library />} />
            {/* Full-page interactive deep review — opened in a new tab from a
                row's "Open full review ↗" (replaces the old static HTML brief as
                the in-app open target; the HTML artifact still ships for Zotero). */}
            <Route path="/paper/:itemKey" element={<PaperReviewPage />} />
            {/* Annotate folded into Library's Batch-label mode. */}
            <Route path="/annotate" element={<RedirectTo to="/library" extra={{ mode: 'batch' }} />} />

            {/* Ops: Feed review + Triage jobs + Pending changes, one page. */}
            <Route path="/ops" element={<Ops />} />
            {/* The three former power-tool pages → Ops tabs. Query strings (e.g.
                ?state=gate_rejected) ride along so deep links keep working. */}
            <Route path="/review" element={<RedirectTo to="/ops" extra={{ tab: 'review' }} />} />
            <Route path="/triage" element={<RedirectTo to="/ops" extra={{ tab: 'triage' }} />} />
            <Route path="/pending" element={<RedirectTo to="/ops" extra={{ tab: 'pending' }} />} />

            {/* Re-label Audit de-linked from the nav (Increment 3). The page is
                kept and still routable; /audit redirects to Library so an old
                bookmark never 404s. */}
            <Route path="/audit-page" element={<Audit />} />
            <Route path="/audit" element={<Navigate to="/library" replace />} />

            <Route path="*" element={<Navigate to="/library" replace />} />
          </Routes>
        </SetupGate>
      </main>
    </div>
  );
}
