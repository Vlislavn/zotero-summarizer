import { expect, it, vi } from 'vitest';
import { QueryClient } from '@tanstack/react-query';
import { doctorQueryOptions, doctorVerificationOptions, invalidateSetupDoctor } from './setupDoctorState.js';

const key = ['setup-doctor'];
const client = () => new QueryClient({ defaultOptions: { queries: { retry: false } } });

it.each([403, 422])('keeps the unchanged report and current owner after definite rejection %s', async (status) => {
  const cache = client();
  const ready = { ready: true, status: 'ready' };
  cache.setQueryData(key, ready);
  cache.setQueryData(['setup-status'], ready);
  const cancel = vi.spyOn(cache, 'cancelQueries');
  const invalidate = vi.spyOn(cache, 'invalidateQueries');
  await invalidateSetupDoctor(cache, { status });
  expect(cache.getQueryData(key)).toBe(ready);
  expect(cache.getQueryData(['setup-status'])).toBe(ready);
  expect(cancel).not.toHaveBeenCalled();
  expect(invalidate).not.toHaveBeenCalled();
  const run = doctorVerificationOptions(cache, vi.fn());
  const owner = run.onMutate();
  await invalidateSetupDoctor(cache, { status });
  run.onSuccess(ready, null, owner);
  expect(cache.getQueryData(key).ready).toBe(true);
  cache.clear();
});

it.each(['AI', 'credential', 'path', 'source'])('cannot revive Ready from a verification completed before a %s change', async () => {
  const cache = client();
  const options = doctorVerificationOptions(cache, vi.fn());
  const owner = options.onMutate();
  await invalidateSetupDoctor(cache);

  options.onSuccess({ ready: true, checks: ['old result'] }, null, owner);

  expect(cache.getQueryData(key).ready).toBe(false);
  cache.clear();
});

it('does not fence another QueryClient or a current completion', () => {
  const first = client();
  const second = client();
  const run = doctorVerificationOptions(first, vi.fn());
  const owner = run.onMutate();
  void invalidateSetupDoctor(second);
  run.onSuccess({ ready: true }, null, owner);

  expect(first.getQueryData(key).ready).toBe(true);
  expect(second.getQueryData(key).ready).toBe(false);
  first.clear(); second.clear();
});

it('cancels an old status read even if its transport ignores abort', async () => {
  const cache = client();
  let finish;
  const read = cache.fetchQuery(doctorQueryOptions(cache, () => new Promise((resolve) => { finish = resolve; })));
  const settled = read.catch(() => null);
  await invalidateSetupDoctor(cache);
  finish({ ready: true });
  await settled;

  expect(cache.getQueryData(key).ready).toBe(false);
  cache.clear();
});

it('a fresh server status retires a pending verification result', async () => {
  const cache = client();
  const run = doctorVerificationOptions(cache, vi.fn());
  const owner = run.onMutate();
  await cache.fetchQuery(doctorQueryOptions(cache, async () => ({ ready: false, status: 'needs_action' })));
  run.onSuccess({ ready: true }, null, owner);

  expect(cache.getQueryData(key).ready).toBe(false);
  cache.clear();
});
