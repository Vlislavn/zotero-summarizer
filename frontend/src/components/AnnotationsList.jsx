// List of Zotero annotations attached to a paper.
// Props: { annotations } — each entry shape:
//   { text, comment, page_label, type, color, date_added }

export default function AnnotationsList({ annotations = [] }) {
  const list = Array.isArray(annotations) ? annotations : [];
  return (
    <div>
      <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-500 mb-2">
        Annotations ({list.length})
      </h3>
      {list.length === 0 ? (
        <div className="text-xs text-slate-400 italic">No annotations.</div>
      ) : (
        <ul className="space-y-2">
          {list.map((a, i) => (
            <li
              key={i}
              className="border border-slate-200 rounded-lg p-2.5 bg-white"
              style={a.color ? { borderLeft: `4px solid ${a.color}` } : undefined}
            >
              {a.text && (
                <div className="text-sm text-slate-800 italic whitespace-pre-line">
                  “{a.text}”
                </div>
              )}
              {a.comment && (
                <div className="text-sm text-slate-700 mt-1.5 whitespace-pre-line">
                  {a.comment}
                </div>
              )}
              <div className="text-[10px] mono text-slate-400 mt-2 flex flex-wrap items-center gap-x-2 gap-y-0.5">
                {a.page_label && <span>p. {a.page_label}</span>}
                {a.type && <span>· {a.type}</span>}
                {a.color && (
                  <span className="flex items-center gap-1">
                    ·
                    <span
                      className="inline-block w-2.5 h-2.5 rounded-full border border-slate-300"
                      style={{ backgroundColor: a.color }}
                    />
                    <span>{a.color}</span>
                  </span>
                )}
                {a.date_added && <span>· {a.date_added}</span>}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
