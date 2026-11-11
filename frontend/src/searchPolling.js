export function pollSessionUntilDone(id, { fetchSession, onSession, onError, interval = 3000, stopOnError = false }) {
  let active = true;
  const controller = new AbortController();
  let timer;

  const poll = async () => {
    try {
      const session = await fetchSession(id, { signal: controller.signal });
      if (!active) return;
      onSession(session);
      if (session.status !== 'reviewing') return;
    } catch (error) {
      if (!active) return;
      onError(error);
      if (stopOnError) return;
    }
    if (active) timer = setTimeout(poll, interval);
  };

  timer = setTimeout(poll, interval);
  return () => {
    active = false;
    controller.abort();
    clearTimeout(timer);
  };
}
