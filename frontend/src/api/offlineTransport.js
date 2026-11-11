import { noteResponse, publishStatus } from '../offlineStore.js';

const DEFAULT_SYNC_TIMEOUT_MS = 15_000;
const MAX_SYNC_TIMEOUT_MS = 60_000;

export function offlineRequestBudget() {
  const value = Number(import.meta.env.VITE_SYNC_TIMEOUT_MS ?? DEFAULT_SYNC_TIMEOUT_MS);
  if (!Number.isFinite(value) || value <= 0 || value > MAX_SYNC_TIMEOUT_MS) {
    throw new Error('Invalid sync timeout budget (VITE_SYNC_TIMEOUT_MS)');
  }
  return value;
}

export async function fetchTransport(path, options) {
  try {
    const response = await fetch(path, options);
    noteResponse(); // any HTTP answer (200 or 503) proves the server is there: reset the silent-timeout count
    return response;
  } catch (cause) {
    const error = new Error(cause?.message || String(cause));
    error.name = cause?.name || 'Error';
    error.transportFailure = !options.signal?.aborted;
    error.cause = cause;
    throw error;
  }
}

export async function offlineRequest(path, options, decode) {
  const budget = offlineRequestBudget();
  if (options.signal?.aborted) throw new DOMException('Request aborted', 'AbortError');
  const controller = new AbortController();
  const abort = () => controller.abort();
  options.signal?.addEventListener('abort', abort, { once: true });
  const timeout = setTimeout(abort, budget);
  try {
    let response;
    try {
      response = await fetchTransport(path, { ...options, signal: controller.signal });
    } catch (error) {
      const timedOut = controller.signal.aborted && !options.signal?.aborted;
      if (timedOut) error.transportFailure = true;
      // A read deadline with no answer is a slow server. Only sync's own silent deadlines count towards
      // 'unavailable' (syncClient → publishNoResponse), so parallel page reads cannot escalate an overloaded server.
      if (timedOut) await publishStatus('', 'busy');
      else if (error.transportFailure) await publishStatus('Server unavailable', 'unavailable');
      throw error;
    }
    return await decode(response);
  } finally {
    clearTimeout(timeout);
    options.signal?.removeEventListener('abort', abort);
  }
}
