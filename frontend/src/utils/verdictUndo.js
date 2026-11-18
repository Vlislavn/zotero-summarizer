import { deleteVerdict, submitVerdict } from '../api/goldenApi.js';
import { queueRejectTag } from '../api/libraryApi.js';

// One write path for a verdict that can be taken back (F-final-plan H02). Writes per paper run
// strictly in order, so an Undo never overtakes its own still-running POST. Undo restores the
// prior verdict (POST its priority + comment) or DELETEs when there was none. The verdict is
// saved at once; the ❌ reject-tag request waits out the Undo window (an applied ❌ would hide the
// paper and teach the model a strong negative) and is dropped by Undo. Reason chips collect in
// the window and go out as ONE extra POST when it closes. The window closes early on the next
// verdict and on pagehide.
export const UNDO_WINDOW_MS = 8000;

const tails = new Map();    // item key -> last queued write
const windows = new Map();  // item key -> open Undo window
let hooked = false;

function enqueue(key, write) {
  const run = (tails.get(key) || Promise.resolve()).then(write);
  const tail = run.catch(() => {});
  tails.set(key, tail);
  tail.then(() => { if (tails.get(key) === tail) tails.delete(key); });
  return run;
}

function closeAll() {
  [...windows.values()].forEach((win) => win.close());
}

export function decide(key, priority, { prior = null, surface, comment = '', onWarning } = {}) {
  closeAll();  // the previous toast is replaced: its ❌ request / reasons go out now
  if (typeof window !== 'undefined' && !hooked) {
    hooked = true;
    window.addEventListener('pagehide', closeAll);
  }
  let reasons = [];
  let undone = null;
  let timer = null;
  let closed = false;
  const done = enqueue(key, () => submitVerdict({ item_key: key, user_priority: priority, comment, surface }));
  done.catch(() => {});

  async function sendExtras() {
    const saved = await done.catch(() => null);
    if (!saved) return;  // the verdict itself failed: nothing to annotate or tag
    if (reasons.length) {
      await submitVerdict({ item_key: key, user_priority: priority, comment: reasons.join(', '), surface });
    }
    if (priority !== 'dont_read' || saved.saved_offline || key.startsWith('feed:')) return;
    try { await queueRejectTag(key); } catch (error) {
      onWarning?.(`Verdict saved, but reject tag was not queued: ${error?.message || error}`);
    }
  }
  const win = {
    close() {
      if (closed) return;
      closed = true;
      clearTimeout(timer);
      if (windows.get(key) === win) windows.delete(key);
      enqueue(key, sendExtras).catch((error) => onWarning?.(error?.message || String(error)));
    },
  };
  windows.set(key, win);
  timer = setTimeout(win.close, UNDO_WINDOW_MS);

  return {
    done,
    setReasons(next) { reasons = [...new Set(next)]; },
    undo() {
      if (undone) return undone;
      closed = true;
      clearTimeout(timer);
      if (windows.get(key) === win) windows.delete(key);
      undone = enqueue(key, () => (prior?.user_priority
        ? submitVerdict({ item_key: key, user_priority: prior.user_priority, comment: prior.comment ?? '', surface })
        : deleteVerdict(key)));
      return undone;
    },
  };
}
