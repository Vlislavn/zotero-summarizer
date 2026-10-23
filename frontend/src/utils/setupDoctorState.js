// One request owner per existing QueryClient; never revive a pre-edit Ready result.
const owners = new WeakMap();
const key = ['setup-doctor'];

function retire(client) {
  const owner = {};
  owners.set(client, owner);
  return owner;
}

export function doctorQueryOptions(client, fetchStatus) {
  return { queryKey: key, queryFn: ({ signal }) => {
    retire(client);
    return fetchStatus({ signal });
  } };
}

export function invalidateSetupDoctor(client) {
  retire(client);
  void client.cancelQueries({ queryKey: key });
  client.setQueryData(key, { ready: false, status: 'not_started', checks: [], modes: {} });
  void client.cancelQueries({ queryKey: ['setup-status'] });
  client.setQueryData(['setup-status'], (old) => old ? { ...old, ready: false } : old);
  void client.invalidateQueries({ queryKey: ['setup-status'] });
  return client.invalidateQueries({ queryKey: key });
}

export function doctorVerificationOptions(client, runChecks) {
  return {
    mutationKey: ['setup-doctor-run'],
    mutationFn: (ids) => runChecks(ids),
    onMutate: () => {
      const owner = retire(client);
      void client.cancelQueries({ queryKey: key });
      client.setQueryData(key, (old) => ({ ...old, ready: false, status: 'running' }));
      return owner;
    },
    onSuccess: (result, _ids, owner) => {
      if (owners.get(client) === owner) client.setQueryData(key, result);
    },
    onSettled: () => client.invalidateQueries({ queryKey: key }),
  };
}
