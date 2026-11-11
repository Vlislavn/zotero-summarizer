import { afterEach, expect, it, vi } from 'vitest';
import { pollSessionUntilDone } from './searchPolling.js';

afterEach(() => vi.useRealTimers());

it('discovery failure stops automatic retries, without publishing cancellation or success', async () => {
  vi.useFakeTimers();
  const failure = new Error('status offline');
  const fetchSession = vi.fn().mockRejectedValue(failure);
  const onError = vi.fn(), onSession = vi.fn();
  pollSessionUntilDone('discovery-id', { fetchSession, onSession, onError, stopOnError: true });

  await vi.advanceTimersByTimeAsync(30_000);

  expect(fetchSession).toHaveBeenCalledTimes(1);
  expect(onError).toHaveBeenCalledTimes(1);
  expect(onError).toHaveBeenCalledWith(failure);
  expect(onSession).not.toHaveBeenCalled();
});

it('cancellation invokes abort on the in-flight owner and fences its late result', async () => {
  vi.useFakeTimers();
  let finish;
  const fetchSession = vi.fn(() => new Promise(resolve => { finish = resolve; }));
  const onSession = vi.fn(), onError = vi.fn();
  const cancel = pollSessionUntilDone('discovery-id', { fetchSession, onSession, onError, stopOnError: true });
  await vi.advanceTimersByTimeAsync(3000);
  const signal = fetchSession.mock.calls[0][1].signal;

  cancel();
  finish({ id: 'discovery-id', status: 'reviewed' });
  await vi.advanceTimersByTimeAsync(30_000);

  expect(signal.aborted).toBe(true);
  expect(fetchSession).toHaveBeenCalledTimes(1);
  expect(onSession).not.toHaveBeenCalled();
  expect(onError).not.toHaveBeenCalled();
});

it('ordinary Search still retries transient read failures and stops on terminal review', async () => {
  vi.useFakeTimers();
  const fetchSession = vi.fn().mockRejectedValueOnce(new Error('temporary'))
    .mockResolvedValueOnce({ id: 'ordinary', status: 'reviewing' })
    .mockResolvedValueOnce({ id: 'ordinary', status: 'reviewed' });
  const onSession = vi.fn(), onError = vi.fn();
  pollSessionUntilDone('ordinary', { fetchSession, onSession, onError });

  await vi.advanceTimersByTimeAsync(30_000);

  expect(fetchSession).toHaveBeenCalledTimes(3);
  expect(onError).toHaveBeenCalledTimes(1);
  expect(onSession.mock.calls.map(([dto]) => dto.status)).toEqual(['reviewing', 'reviewed']);
});
