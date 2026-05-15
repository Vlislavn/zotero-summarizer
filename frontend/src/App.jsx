import AnnotationVerdict from './pages/AnnotationVerdict.jsx';

export default function App() {
  return (
    <div className="min-h-screen px-4 py-5 max-w-[1400px] mx-auto">
      <header className="glass border border-slate-200 rounded-2xl shadow-lg p-4 mb-5">
        <div className="flex items-center justify-between gap-4">
          <div>
            <h1 className="text-2xl font-bold text-slate-900">Label Audit</h1>
            <p className="text-sm text-slate-600">
              Review annotations, notes, and tags to confirm or correct triage verdicts.
            </p>
          </div>
          <div className="text-xs text-slate-500">
            Zotero Summarizer · <span className="mono">/annotate</span>
          </div>
        </div>
      </header>
      <main>
        <AnnotationVerdict />
      </main>
    </div>
  );
}
