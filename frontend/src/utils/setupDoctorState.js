// Request ownership is scoped to the existing QueryClient, not persisted app state.
const currentRequests = new WeakMap();
const DOCTOR_KEY = ['setup-doctor'];

function retireRequests(queryClient) {
  const request = {};
  currentRequests.set(queryClient, request);
  return request;
}

export function doctorQueryOptions(queryClient, fetchStatus) {
  return {
    queryKey: DOCTOR_KEY,
    queryFn: ({ signal }) => {
      retireRequests(queryClient);
      return fetchStatus({ signal });
    },
  };
}

export function invalidateSetupDoctor(queryClient) {
  retireRequests(queryClient);
  // React Query cancellation retires the read even if its transport ignores abort.
  void queryClient.cancelQueries({ queryKey: DOCTOR_KEY });
  queryClient.setQueryData(DOCTOR_KEY, { ready: false, status: 'not_started', checks: [], modes: {} });
  return queryClient.invalidateQueries({ queryKey: DOCTOR_KEY });
}

// Only these prewrite branches are proven for config/path/RSS writes. A generic
// 4xx (notably FileNotFoundError -> 404) is not rollback evidence. Credential
// saves invalidate server health before validation and must bypass this predicate.
export function invalidateSetupDoctorAfterWriteError(queryClient, error) {
  const status = error?.status;
  if (Number.isInteger(status) && [403, 409, 422].includes(status)) return false;
  void invalidateSetupDoctor(queryClient);
  return true;
}

export function doctorVerificationOptions(queryClient, runChecks) {
  return {
    mutationKey: ['setup-doctor-run'],
    mutationFn: (ids) => runChecks(ids),
    onMutate: () => {
      const request = retireRequests(queryClient);
      void queryClient.cancelQueries({ queryKey: DOCTOR_KEY });
      queryClient.setQueryData(DOCTOR_KEY, (previous) => previous ? { ...previous, ready: false } : previous);
      return request;
    },
    onSuccess: (result, _ids, request) => {
      if (currentRequests.get(queryClient) === request) queryClient.setQueryData(DOCTOR_KEY, result);
    },
  };
}
