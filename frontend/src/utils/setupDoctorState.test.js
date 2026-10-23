import { expect, it, vi } from 'vitest';
import { QueryClient, QueryObserver } from '@tanstack/react-query';
import { doctorQueryOptions, doctorVerificationOptions, invalidateSetupDoctorAfterWriteError } from './setupDoctorState.js';

const ready = { ready: true, status: 'ready', checks: [], modes: {} };
const changed = { ready: false, status: 'not_started', checks: [
  { id: 'rss_source', status: 'not_started', message: 'Changed source' },
], modes: {} };
const client = () => new QueryClient({ defaultOptions: {
  queries: { retry: false, gcTime: Infinity }, mutations: { retry: false },
} });

it.each([403, 409, 422])('preserves valid health for traced prewrite rejection %s', (status) => {
  const queryClient = client();
  try {
    queryClient.setQueryData(['setup-doctor'], ready);
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
    const cancel = vi.spyOn(queryClient, 'cancelQueries');
    expect(invalidateSetupDoctorAfterWriteError(queryClient, { status })).toBe(false);
    expect(queryClient.getQueryData(['setup-doctor'])).toEqual(ready);
    expect(invalidate).not.toHaveBeenCalled();
    expect(cancel).not.toHaveBeenCalled();
  } finally { queryClient.clear(); }
});

it.each([
  undefined, null, '422', 422.5, true, false, NaN, Infinity,
  0, 200, 302, 400, 401, 404, 408, 500, 503,
])('retires health for uncertain or unsupported status %s', (status) => {
  const queryClient = client();
  try {
    queryClient.setQueryData(['setup-doctor'], ready);
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
    expect(invalidateSetupDoctorAfterWriteError(queryClient, { status })).toBe(true);
    expect(queryClient.getQueryData(['setup-doctor'])?.ready).toBe(false);
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['setup-doctor'] });
  } finally { queryClient.clear(); }
});

it.each([new TypeError('Reply lost'), new SyntaxError('Invalid JSON'), null])(
  'treats transport/JSON/missing errors as uncertain without changing the error', (error) => {
    const queryClient = client();
    try {
      queryClient.setQueryData(['setup-doctor'], ready);
      const before = error?.message;
      expect(invalidateSetupDoctorAfterWriteError(queryClient, error)).toBe(true);
      expect(queryClient.getQueryData(['setup-doctor'])?.ready).toBe(false);
      expect(error?.message).toBe(before);
    } finally { queryClient.clear(); }
  },
);

it('retires a verification on uncertain write alone without blocking another client completion', () => {
  const owner = client();
  const other = client();
  const runChecks = vi.fn();
  try {
    owner.setQueryData(['setup-doctor'], ready);
    other.setQueryData(['setup-doctor'], ready);
    const first = doctorVerificationOptions(owner, runChecks);
    const second = doctorVerificationOptions(other, runChecks);
    const ownerRequest = first.onMutate();
    const otherRequest = second.onMutate();
    invalidateSetupDoctorAfterWriteError(owner, new TypeError('Lost ACK'));
    first.onSuccess(ready, null, ownerRequest);
    second.onSuccess(ready, null, otherRequest);
    expect(owner.getQueryData(['setup-doctor'])?.ready).toBe(false);
    expect(other.getQueryData(['setup-doctor'])).toEqual(ready);
    expect(runChecks).not.toHaveBeenCalled();
  } finally { owner.clear(); other.clear(); }
});

it('fences old verification and an abort-ignoring read, passively refetching only the owning client', async () => {
  const owner = client();
  const other = client();
  let resolveOld;
  const old = new Promise((resolve) => { resolveOld = resolve; });
  const fetchStatus = vi.fn().mockReturnValueOnce(old).mockResolvedValueOnce(changed);
  const runChecks = vi.fn();
  owner.setQueryData(['setup-doctor'], ready);
  other.setQueryData(['setup-doctor'], ready);
  const observer = new QueryObserver(owner, { ...doctorQueryOptions(owner, fetchStatus), staleTime: Infinity });
  const unsubscribe = observer.subscribe(() => {});
  try {
    const verification = doctorVerificationOptions(owner, runChecks);
    const request = verification.onMutate();
    const pending = observer.refetch();
    await vi.waitFor(() => expect(fetchStatus).toHaveBeenCalledTimes(1));
    const signal = fetchStatus.mock.calls[0][0].signal;
    expect(invalidateSetupDoctorAfterWriteError(owner, new TypeError('Lost ACK'))).toBe(true);
    expect(signal.aborted).toBe(true);
    await vi.waitFor(() => expect(owner.getQueryData(['setup-doctor'])).toEqual(changed));
    resolveOld(ready);
    await old;
    await pending;
    verification.onSuccess(ready, null, request);
    expect(owner.getQueryData(['setup-doctor'])).toEqual(changed);
    expect(other.getQueryData(['setup-doctor'])).toEqual(ready);
    expect(fetchStatus).toHaveBeenCalledTimes(2);
    expect(runChecks).not.toHaveBeenCalled();
  } finally { unsubscribe(); owner.clear(); other.clear(); }
});
