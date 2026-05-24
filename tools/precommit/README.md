# tools/precommit — repo guardrail checks

Stdlib-only scripts wired by `.pre-commit-config.yaml`. Run all:
`pre-commit run --all-files`.

| script | enforces |
|---|---|
| `check_file_loc.py` | `.py` ≤ 500 LOC; legacy files frozen at their `loc_allowlist.txt` ceiling |
| `check_import_policy.py` | the layered-import rules + "new service modules go in a domain subpackage" |
| `check_module_readme.py` | every package has a README; editing a package's code re-stages its README |
| `loc_allowlist.txt` | grandfathered oversized files (path + frozen ceiling) — shrink to empty |

See [docs/architecture.md](../../docs/architecture.md) for the rules in context.
