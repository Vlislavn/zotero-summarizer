export function GroupHeader({ children, action = null }) {
  return (
    <div className="mb-2 mt-3 flex items-center justify-between gap-2">
      <h3 className="text-[11px] font-semibold uppercase tracking-[0.06em] text-slate-500">{children}</h3>
      {action}
    </div>
  );
}

export function GroupAction({ expanded, onClick, children }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-expanded={expanded}
      className="shrink-0 px-2 py-1.5 -my-1 rounded-md text-xs font-medium text-teal-700 hover:bg-teal-50"
    >
      {children}
    </button>
  );
}
