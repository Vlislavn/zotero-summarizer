import Sheet from '../ui/Sheet.jsx';
import Button from '../ui/Button.jsx';

// The Library page's one confirmation: a bottom Sheet with what will happen (a few lines, the backup one last).
// Focus starts on the title (Sheet), so Enter never writes by accident; Esc, the backdrop and Close all cancel.
export default function ConfirmSheet({ title, lines, confirm, onConfirm, onCancel }) {
  return (
    <Sheet title={title} variant="bottom" onClose={onCancel}>
      <ul className="list-disc space-y-1.5 pl-5 text-sm text-slate-700">
        {lines.map((line) => <li key={line}>{line}</li>)}
      </ul>
      <div className="flex flex-wrap justify-end gap-2 pt-4">
        <Button variant="secondary" className="min-h-11" onClick={onCancel}>Cancel</Button>
        <Button className="min-h-11" onClick={onConfirm}>{confirm}</Button>
      </div>
    </Sheet>
  );
}
