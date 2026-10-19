# Tests — explicit local/gateway boundary

```text
actual app factory + fixture SPA   -> synthetic admission/denial matrix
actual Uvicorn + private socket    -> HTTP/WS + owner/lifetime/restart oracles
actual Vite proxy + local guard    -> same-origin POST / foreign-Origin denial
```

Gateway tests use disposable project/data directories, generic owner/origin
fixtures and owned subprocesses. No Tailscale/account/ACL/native-library/model
activation is performed. `test_local_access_boundary.py` and
`test_make_launchers.py` preserve the local path and unsafe-bind/shortcut refusal;
`test_serve_free_port.py` retains the no-foreign-process-killing invariant.
`test_mobile_access_boundary.py` has synthetic transport adaptation, not a real
network identity. `test_gateway_app_surface.py` exercises the actual factory and
fixture assets/public files, including admitted/refused WebSockets.

`test_gateway_owned_paths.py` checks modes, ancestry, symlinks, foreign/replaced
paths, idempotent cleanup, startup failure and bounded shutdown configuration.
`test_gateway_real_transport.py` uses real Uvicorn Unix HTTP/WS with handcrafted
fixture identity headers. `_gateway_process.py` binds child imports to the tested
source and records their roots; it substitutes a trivial application lifespan.
Signal assertions require non-success, owned socket removal before fixture cleanup
and successful rebinding, not one platform-specific signal/exception exit encoding.
`test_gateway_signal_control.py` retains stock-versus-gateway interruption controls.

`test_gateway_vite_proxy.py` imports the actual Vite configuration and verifies a
real local POST through it. Its explicit Node/frontend-dependency absence skip is
not a passing integration test; install existing project dependencies and run this
case before accepting development wiring. Caches/logs are fixture-owned, not written
through shared dependency directories. Real subprocess tests require POSIX sockets.
They use bounded readiness steps and named environment-overridable safety caps.

These machine contracts do not certify a real trusted TLS gateway, installed
Tailscale build/header sanitization, private device policy, device-specific access,
active-stream revocation, physical iPhone installation/offline behavior, downstream
model shutdown, scientific accuracy or full native-data delivery. See
[deployment boundary](../docs/local-deployment-boundary.md).
