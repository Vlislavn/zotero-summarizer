// A simple shimmer placeholder block for loading states. Tailwind-only
// (`animate-pulse`), themed with the same slate vocabulary as the rest of the
// app. Use one or several to sketch the shape of content while it loads instead
// of a bare "Loading…" line.
//
// Defaults to a single full-width line; pass `width`/`height` Tailwind classes
// to size it, or `count` to render a stack of lines.
export default function Skeleton({
  width = 'w-full',
  height = 'h-4',
  rounded = 'rounded-md',
  count = 1,
  className = '',
}) {
  const base = `animate-pulse bg-slate-200 ${width} ${height} ${rounded}`;
  if (count <= 1) {
    return (
      <div
        aria-hidden="true"
        className={`${base}${className ? ` ${className}` : ''}`}
      />
    );
  }
  return (
    <div aria-hidden="true" className={`space-y-2${className ? ` ${className}` : ''}`}>
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className={base} />
      ))}
    </div>
  );
}
