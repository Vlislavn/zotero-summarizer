import { afterEach, expect, it, vi } from 'vitest';
import { pollSessionUntilDone } from './searchPolling.js';

afterEach(() => vi.useRealTimers());

it('does not overlap polls and ignores an in-flight response after stop', async () => {
  vi.useFakeTimers();
  let finish;
  const fetchSession = vi.fn(() => new Promise((resolve) => { finish = resolve; }));
  const onSession = vi.fn();
  const stop = pollSessionUntilDone('s1', { fetchSession, onSession, onError: vi.fn() });

  await vi.advanceTimersByTimeAsync(3000);
  expect(fetchSession).toHaveBeenCalledTimes(1);
  stop();
  finish({ status: 'reviewing' });
  await Promise.resolve();
  await vi.advanceTimersByTimeAsync(6000);

  expect(fetchSession).toHaveBeenCalledTimes(1);
  expect(onSession).not.toHaveBeenCalled();
});

it('retries a transient error and stops after a terminal session', async () => {
  vi.useFakeTimers();
  const fetchSession = vi.fn()
    .mockRejectedValueOnce(new Error('temporary'))
    .mockResolvedValueOnce({ status: 'reviewing' })
    .mockResolvedValueOnce({ status: 'reviewed' });
  const onSession = vi.fn();
  const onError = vi.fn();
  pollSessionUntilDone('s1', { fetchSession, onSession, onError });

  await vi.advanceTimersByTimeAsync(9000);

  expect(onError).toHaveBeenCalledTimes(1);
  expect(onSession.mock.calls.map(([session]) => session.status)).toEqual(['reviewing', 'reviewed']);
  expect(fetchSession).toHaveBeenCalledTimes(3);
});
