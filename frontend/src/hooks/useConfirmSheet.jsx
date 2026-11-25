import { useCallback, useState } from 'react';
import ConfirmSheet from '../components/library/ConfirmSheet.jsx';

// `const [ask, dialog] = useConfirmSheet()`: `await ask(sheet)` shows the sheet and answers true (confirmed)
// or false (cancelled); render `{dialog}` once. Replaces window.confirm where the answer gates a write.
export default function useConfirmSheet() {
  const [open, setOpen] = useState(null);
  const ask = useCallback((sheet) => new Promise((resolve) => setOpen({ sheet, resolve })), []);
  const answer = (value) => { open.resolve(value); setOpen(null); };
  const dialog = open && <ConfirmSheet {...open.sheet} onConfirm={() => answer(true)} onCancel={() => answer(false)} />;
  return [ask, dialog];
}
