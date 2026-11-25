import { useEffect, useState } from 'react';

// Every exit from the open paper (close, ‹ ›, another row, Select) goes through `guarded`: an
// unsaved note turns it into a question inside the detail (`leave`) instead of losing the note.
export default function useLeaveGuard(openKey) {
  const [notesDirty, setNotesDirty] = useState(false);
  const [leave, setLeave] = useState(null);  // the exit waiting for «Leave without saving?»
  function guarded(action) {
    if (notesDirty) setLeave(() => action); else action();
  }
  function leaveNow() {
    const action = leave;
    setLeave(null);
    setNotesDirty(false);
    action?.();
  }
  useEffect(() => { setNotesDirty(false); setLeave(null); }, [openKey]);
  return { notesDirty, setNotesDirty, leave, setLeave, guarded, leaveNow };
}
