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
                api_key_present  (Zotero/reachable/classifier/subsystems advisory)
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
 calibration.py ─ per-user calibration → data/calibration.json (precedence between
               goals.yaml and ZS_* env; applied by config_overrides.apply_calibration).
               TIER 1: tier1_env_calibrate — one bounded completion measures the
               deep-review endpoint throughput → picks the lean/full text+consistency
               profile (auto-detecting the manual lean_deep_review flag; a REMOTE
               endpoint is always 'full'). Idempotent on the `tier1` stamp.
               TIER 2: tier2_calibrate / run_full_calibration ("Calibrate to my setup",
               via the `calibrate` CLI + POST /api/setup/calibrate) — sweeps the
               deep-review text budget on the user's endpoint, scoring deterministic
               digest completeness (no judge) and picking the FASTEST budget at equal
               completeness (latency-as-cost). Foreground + memory-pre-flighted for a
               local provider; remotes load no local model. ``run_full_calibration``
               takes an optional ``progress`` callback ({phase, completed, total}) so the
               UI can show live "reviewed N of M" (total = budgets × papers).
               upsert_calibration_entries is the shared writer for every tier.
 calibration_job.py ─ background-thread wrapper for "Calibrate to my setup" so the card
               POLLS live progress instead of blocking: start() kicks the sweep off on a
               daemon thread (single-flight) + threads a progress callback into the module
               job dict; status() returns {status, completed, total, phase, result, error}
               (same run+poll shape deep_review uses). Backs POST /api/setup/calibrate +
               GET /api/setup/calibrate/status. The "no built briefs" precondition surfaces
               as a status `error` the card turns into "open a paper's deep review first".
 profiles.py ─ DEPLOYMENT profiles (fully-local vs hybrid local+API) + stage-cost
               measurement. PROFILES presets → apply_profile rewrites llm_routing (which
               stage local vs API) + sets lean_deep_review (superficial local / deep API).
               set_profile / detect_profile back the `profile` CLI + /api/setup/profile[s].
               measure_stage_costs (real tokens+secs per stage×provider) → summarize_costs
               + recommend_profile (pure, tested) back `profile --measure`. Remote-only by
               default — a local gen loads a multi-GB model (swap spike); --include-local opts in.
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
