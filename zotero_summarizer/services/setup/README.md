# services/setup — first-run setup + onboarding

The primitives behind the config-UX simplification: one readiness probe, a
read-only Zotero-dir detector, an allowlisted `.env` path writer, a dry-run
config validator, and the Phase-0 boot bootstrap. Both the HTTP layer
(`api/routes/setup.py`) and the `zotero-summarizer setup` CLI call THESE — no
logic is duplicated between the two front-ends.

```
                   ┌──────────────── api/routes/setup.py ────────────────┐
                   │ GET  /api/setup/status         → status.get_setup_status
                   │ GET  /api/setup/detect-zotero  → detect.detect_zotero_data_dirs
                   │ PUT  /api/setup/paths          → env_writer.write_env_paths
                   │ POST /api/setup/validate-config→ validate.validate_config_draft
                   └──────────────────────┬──────────────────────────────┘
                                          │ (same fns)
   cli/_setup.py  zotero-summarizer setup ┘

 status.py   ─ read_config + check_reachability(default) + key-PRESENCE bool
              + paths.exists() + zotero_status_payload + feed count + model_card
              + readiness.all_statuses() → SetupStatusResponse.subsystems[]
              → SetupStatusResponse; `ready` = config.valid & goals>0 &
                api_key_present & zotero.db_found  (reachable/classifier/subsystems advisory)
 detect.py   ─ per-OS probe dirs + current settings().zotero_data_dir(source=env)
              → DetectedZoteroDir[], db_exists first. READ-ONLY (Path.exists only).
 env_writer.py ─ _ALLOWED_ENV_KEYS=(PDF_ROOT,ZOTERO_DATA_DIR); reject others (422);
                 path-must-exist (422); byte-for-byte read-modify-write via
                 atomic_write (secrets/comments preserved, NOT dotenv-dumped).
 validate.py ─ GoalsConfig.model_validate(draft) → field_errors; optional probe of
               the default stage (probe_provider + model_list). Persists NOTHING.
 bootstrap.py─ bootstrap_phase0(settings): goals.yaml (if absent) + .env skeleton
               (if absent, COMMENTED secret placeholder — never a real key) +
               migrate DB (if absent, reuses storage.migrations.migrate_existing).
               Idempotent; never overwrites an existing file. Called from serve.
```

## Security invariants (load-bearing)

- **API-key SECRETS never appear in any response, are never written by these
  endpoints, and are never read AS A VALUE.** `api_key_env` is only ever an
  env-var NAME. `status` reports `api_key_present` as a BOOL
  (`bool(os.getenv(name))`); `env_writer` refuses every key outside
  `_ALLOWED_ENV_KEYS` (the two PATH keys), so it can never touch a secret line.
- **`validate.py` and `status.py` mutate NO app state** — no persist, no
  hot-swap. The only writers here are `env_writer` (the two path keys) and
  `bootstrap` (absent files only).

## SANCTIONED EXCEPTION to "all app state lives under `data/`"

`env_writer.write_env_paths` and `bootstrap._bootstrap_env` write `PDF_ROOT` /
`ZOTERO_DATA_DIR` into `.env` at the project root — NOT under `data/`. This is
deliberate and the only carve-out the setup domain makes: those two keys are
filesystem locations the app must read *before* `Settings` is constructed (see
`settings.py::Settings.load`, which `load_dotenv`s `.env` and then reads
`os.getenv("PDF_ROOT"/"ZOTERO_DATA_DIR")`). They cannot live under `data/`
because `data/` itself is derived from the resolved project root. This mirrors
the existing `.env` config carve-out documented in the root `CLAUDE.md`
("Data & config") and `docs/architecture.md`. Secrets are likewise `.env`-only
and are never written here.

**Boundaries:** standard services rules — may import `storage/`,
`integrations/`, `models`, `api.errors`, and other `services/` domains
(`llm`, `model`, `zotero`). Never imports `api.app` / `api.routes`.
