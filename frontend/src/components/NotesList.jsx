import { useState } from 'react';

// List of user-written notes attached to a Zotero item.
// Props: { notes } — each note is { title?, text?, html?, date_added? }.
// Long notes are truncated to ~6 lines and expand on click.

function stripHtml(html) {
  return String(html)
    .replace(/<br\s*\/?>/gi, '\n')
    .replace(/<\/p>/gi, '\n')
    .replace(/<[^>]+>/g, '')
    .trim();
}

function NoteCard({ note }) {
  const [expanded, setExpanded] = useState(false);
  const body = note.text
    ? String(note.text)
    : note.html
    ? stripHtml(note.html)
    : '';
  const lineCount = body.split('\n').length;
  const isLong = lineCount > 6 || body.length > 600;

  return (
    <li className="border border-slate-200 rounded-lg p-2.5 bg-white">
      {note.title && (
        <div className="text-sm font-semibold text-slate-900 mb-1">{note.title}</div>
      )}
      <div
        className={`text-sm text-slate-700 whitespace-pre-line ${
          !expanded && isLong ? 'line-clamp-6' : ''
        }`}
      >
        {body}
      </div>
      {isLong && (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="mt-1 text-[11px] text-teal-700 hover:text-teal-900 font-medium"
        >
          {expanded ? 'Show less' : 'Show more'}
        </button>
      )}
      {note.date_added && (
        <div className="text-[10px] mono text-slate-400 mt-2">{note.date_added}</div>
      )}
    </li>
  );
}

export default function NotesList({ notes = [] }) {
  const list = Array.isArray(notes) ? notes : [];
  return (
    <div>
      <h3 className="text-xs font-bold uppercase tracking-wider text-slate-500 mb-2">
        User notes ({list.length})
      </h3>
      {list.length === 0 ? (
        <div className="text-xs text-slate-400 italic">No notes.</div>
      ) : (
        <ul className="space-y-2">
          {list.map((n, i) => (
            <NoteCard key={i} note={n} />
          ))}
        </ul>
      )}
    </div>
  );
}
