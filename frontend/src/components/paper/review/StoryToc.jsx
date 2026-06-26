import useScrollSpy from '../../../hooks/useScrollSpy.js';

// Sticky left rail for the story page: jump to a zone and see where you are.
// Coarse zone anchors (not 14 section links — that would be a Choice-Overload
// menu); scroll-spy marks the current zone with the one saturated forest rule.
// Hidden below lg (the zones reflow to one column there).
export default function StoryToc({ items = [] }) {
  const active = useScrollSpy(items.map((i) => i.id));
  if (items.length < 2) return null;
  return (
    <nav className="hidden lg:block sticky top-4 self-start text-[12px] leading-6" aria-label="On this page">
      <div className="mb-1.5 text-[11px] uppercase tracking-[0.08em] font-semibold text-slate-400">On this page</div>
      <ul className="border-l border-slate-200">
        {items.map((it) => {
          const on = it.id === active;
          return (
            <li key={it.id}>
              <a
                href={`#${it.id}`}
                aria-current={on ? 'location' : undefined}
                className={`block -ml-px border-l-2 pl-3 py-0.5 transition-colors ${
                  on ? 'border-teal-600 text-teal-800 font-medium' : 'border-transparent text-slate-500 hover:text-slate-800'
                }`}
              >
                {it.label}
              </a>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
