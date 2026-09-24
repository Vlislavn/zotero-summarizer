export function pollSessionUntilDone(id, { fetchSession, onSession, onError, interval = 3000 }) {
  let active = true;
  let timer;

  const poll = async () => {
    try {
      const session = await fetchSession(id);
      if (!active) return;
      onSession(session);
      if (session.status !== 'reviewing') return;
    } catch (error) {
      if (!active) return;
      onError(error);
    }
    if (active) timer = setTimeout(poll, interval);
  };

  timer = setTimeout(poll, interval);
  return () => {
    active = false;
    clearTimeout(timer);
  };
}
