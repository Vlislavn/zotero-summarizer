import { publishStatus } from '../offlineStore.js';

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
    return await fetch(path, options);
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
      if (controller.signal.aborted && !options.signal?.aborted) error.transportFailure = true;
      if (error.transportFailure) await publishStatus('Server unavailable', 'unavailable');
      throw error;
    }
    return await decode(response);
  } finally {
    clearTimeout(timeout);
    options.signal?.removeEventListener('abort', abort);
  }
}
