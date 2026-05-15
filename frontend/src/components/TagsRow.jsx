// Renders a small row of chips, one per tag.
// Engagement emojis (from services/feedback.py) are emphasized.
// Props: { tags } — array of strings or { tag: string } objects.

export const ENGAGEMENT_EMOJIS = new Set([
  '🧠', '✅', '🗝', '👍', '💡', '👀', '🧪', '🧮',
  '❓', '🧱', '⚡', '👎', '❌', '🥱',
]);

const TAG_PREFIX_RE = /^(zs:|d:)/;

function stripPrefix(label) {
  return label.replace(TAG_PREFIX_RE, '');
}

function findEmoji(label) {
  for (const ch of label) {
    if (ENGAGEMENT_EMOJIS.has(ch)) return ch;
  }
  return null;
}

export default function TagsRow({ tags = [] }) {
  if (!tags || tags.length === 0) {
    return (
      <div className="text-xs text-slate-400 italic">No tags.</div>
    );
  }
  return (
    <div className="flex flex-wrap gap-1.5">
      {tags.map((t, i) => {
        const raw = typeof t === 'string' ? t : t?.tag;
        if (!raw) return null;
        const emoji = findEmoji(raw);
        const display = stripPrefix(raw);
        const isEngagement = Boolean(emoji);
        return (
          <span
            key={`${raw}-${i}`}
            title={raw}
            className={
              isEngagement
                ? 'px-2 py-0.5 rounded-full bg-amber-50 text-amber-900 text-xs border border-amber-200 font-medium'
                : 'px-2 py-0.5 rounded-full bg-slate-100 text-slate-700 text-[11px] border border-slate-200'
            }
          >
            {isEngagement && (
              <span className="mr-1 text-base leading-none align-middle">{emoji}</span>
            )}
            <span className="align-middle">{display}</span>
          </span>
        );
      })}
    </div>
  );
}
