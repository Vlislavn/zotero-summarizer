import { useEffect, useState } from 'react';

// Highlight the section currently in the reading band for the story-page TOC.
// IntersectionObserver only — no scroll listener, no dependency. A section is
// "active" once its top crosses the upper third of the viewport. The effect is
// cleanup-safe (io.disconnect), so React-StrictMode's double invoke is a no-op.
export default function useScrollSpy(ids, { rootMargin = '-30% 0px -60% 0px' } = {}) {
  const [active, setActive] = useState(ids[0] ?? null);
  const key = ids.join('|');
  useEffect(() => {
    const els = ids.map((id) => document.getElementById(id)).filter(Boolean);
    if (!els.length) return undefined;
    const io = new IntersectionObserver(
      (entries) => {
        const top = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0];
        if (top) setActive(top.target.id);
      },
      { rootMargin, threshold: 0 },
    );
    els.forEach((el) => io.observe(el));
    return () => io.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, rootMargin]);
  return active;
}
