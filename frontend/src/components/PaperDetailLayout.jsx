// 3-zone layout for paper-detail panes (Annotate, Library, Triage, Pending).
//
//   ┌───────────────────────────────────┐
//   │ topStrip   (sticky top, z-10)     │ ← title, authors, prestige badge
//   ├───────────────────────────────────┤
//   │                                   │
//   │ children   (scrollable middle)    │ ← abstract, tags, SHAP, notes, ...
//   │                                   │
//   ├───────────────────────────────────┤
//   │ bottomStrip (sticky bottom, z-10) │ ← verdict / actions
//   └───────────────────────────────────┘
//
// Fitts's Law: both the title strip and the verdict panel stay in reach
// regardless of how long the paper's body is. Doherty Threshold: switching
// papers swaps the children without resetting scroll on the chrome.
export default function PaperDetailLayout({
  topStrip,
  bottomStrip,
  children,
  emptyState = null,
  className = '',
}) {
  if (emptyState) return emptyState;

  return (
    <section
      className={[
        'glass rounded-2xl border border-slate-200',
        'p-0 lg:col-span-8',
        'overflow-hidden flex flex-col',
        'max-h-[calc(100vh-7rem)] relative',
        className,
      ].join(' ')}
    >
      {topStrip && (
        <div className="sticky top-0 z-10 bg-white/95 backdrop-blur border-b border-slate-200 px-4 pt-4 pb-3">
          {topStrip}
        </div>
      )}

      <div className="flex-1 overflow-y-auto slim-scroll px-4 py-3 space-y-5">
        {children}
      </div>

      {bottomStrip && (
        <div className="sticky bottom-0 z-10 bg-white/95 backdrop-blur border-t border-slate-200 px-4 pt-3 pb-4">
          {bottomStrip}
        </div>
      )}
    </section>
  );
}
