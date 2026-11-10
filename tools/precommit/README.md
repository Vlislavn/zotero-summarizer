# tools/precommit — repo guardrail checks

Local stdlib guardrail scripts wired by `.pre-commit-config.yaml`, plus the
**deadcode-audit** gates (dead code, duplication, overlaps), which are an external tool.
Enforcement runs explicitly via `make lint`; commit-hook installation is optional.

| check | enforces |
|---|---|
| `check_file_loc.py` | hard `.py` ≤ 500 LOC with no allowlist |
| `check_import_policy.py` | AST-based static import rules (relative, aliased, multi-name and nested imports included); `runtime.py` cannot import `services/` or `api/` (including `api.errors`); services may import only `api.errors`; new service modules go in a domain subpackage |
| `check_module_readme.py` | every package has a README; editing a package's code re-stages its README |
| `check_slop.py` | debug leftovers + complexity limits block; heuristics are advisory (Tier 7, below); existing findings grandfathered |
| `check_allowlists.py` | `reconcile`: flags STALE `slop_allowlist.txt` entries (run by the test suite) |
| `slop_allowlist.txt` / `slop_severity.txt` | grandfathered slop findings (`<path>:<line>:<rule>`, empty) / per-rule severity overrides (`rule=off\|advise\|block`) |
| deadcode-audit (`deadcode-gates` hook) | new orphan public symbols (tests do not count), Vulture ≥80% dead code, redundant transforms — **changed lines vs `BASE`** |

## deadcode-audit (dead code, duplication, overlaps)

[Vlislavn/deadcode-audit](https://github.com/Vlislavn/deadcode-audit) is a dev dependency
that **tracks `main` — there is no pinned commit**. Config: `.deadcode.yml` (`source_roots`
is mandatory: the default scan walks `data/` and hangs) and `scripts/vulture_whitelist.py`
(path-agnostic Vulture names).

- **No allowlists.** Grandfathering is the diff: only changed lines vs `BASE` (default
  `origin/main`) are gated. Touching an old bad line re-flags it.
- **Committed changes only.** The diff gates cannot see staged or untracked files, so they
  run at the `pre-push` stage and in `make lint` (hook `deadcode-gates`), not at commit.
  Commit the work first, then `make lint`. `make lint BASE=<ref>` changes the base.
- **Always latest.** `make lint`, `make install` and `make deadcode-update` run
  `uv lock --upgrade-package deadcode-audit && uv sync` and print the commit in use.
  Offline they warn and keep the installed commit; where deadcode-audit is not installed at all
  (e.g. a bare checkout) the refresh and the `make lint` gates warn and continue (never fail) —
  install it with `make install`; `make deadcode-gates` itself stays blocking. `uv.lock` records the resolved commit;
  it is rewritten by every refresh, never edited by hand.
- **Update + gates:** `make deadcode-update`.
- **Reports (advisory, exit 0):** `make scan` (whole tree: reachability, import cycles,
  overlaps, Vulture, redundancy, slop) and `make scan-diff` (changes vs `BASE`).
  Overlaps run with `--no-embed` (no torch).
- **Vulture** is installed as deadcode-audit's extra; model fields appear only at 60%
  confidence (advisory), so no local pydantic guard is needed for the ≥80% gate.

The import gate also forbids `runtime.py` from importing `services/` or `api/`,
including `api.errors`, at any AST scope. Leaf imports such as
`models.providers` and `TYPE_CHECKING`-only integration/storage annotations remain
permitted.

The import gate parses Python with stdlib AST and resolves relative imports from
the importing package without executing it. Syntax/relative-resolution errors
fail the check; comments and example strings are not imports. Tests exercise the
gate and its CLI with forbidden forms and permitted near-misses. This is a static
import check, not a transitive dependency/effect analyzer or a sandbox: dynamic
imports, callbacks and module objects passed through global state are outside its
guarantee. It does not prevent a writer from being called through those paths.

## AI-slop identification — Tier 7 (`check_slop.py`)

Adopts the deterministic slop/dead-code detectors of [aislop](https://github.com/scanaislop/aislop)
("catch the slop AI agents leave behind") into this repo's stdlib pre-commit style. Same
discipline as the other tiers: each rule encodes a **principle** (a shape / task-class
invariant), every false positive is killed by a **guard** (not a special case), **AST is
preferred over text** (comments are read via `tokenize`, so a marker inside a string or
f-string never trips a comment rule), and a detector never silently no-ops. Two subcommands:

- **`slop-check`** (file-based, **BLOCKs**) — catches **debug-leftover**, a committed
  `breakpoint()` / `pdb.set_trace()` / `ipdb.set_trace()`, plus the objective
  code-in-head complexity limits configured below.
  Guards: `breakpoint` is skipped if shadowed (a domain `def breakpoint(...)`); imports
  are *not* treated as shadows (`import pdb` is how you reach the real debugger).
- **`slop-sweep`** (whole-tree, **advisory**, always exits 0) — reports every heuristic;
  the touched-file hook promotes only the objective complexity limits below to blocking:
  - **swallowed-exception** — `except: pass` (spares `as _:` intentional ignores and
    re-raising/returning handlers; the repo's best-effort WAL-pragma idiom is grandfathered).
  - **silent-recovery** — a handler that only logs without the exception's context (spares
    `.exception()`, `exc_info=`, and any log carrying a dynamic arg or the bound name).
  - **mutable-default** — `def f(x=[])` that is actually mutated in the body (spares
    read-only defaults and `None`-then-init).
  - **todo-stub** — a `TODO`/`FIXME`/`HACK` comment with no linked tracker (`#123`, `gh-…`,
    `JIRA-…`, a URL); common-noun markers (`TEMP`/`STUB`/`PLACEHOLDER`) need a leading label.
  - **comment-slop** — a comment that merely restates the next line (trivial) or narrates
    steps (narrative); spares why-comments, `noqa`/section/decorative/doc-indicator comments,
    and ≥3-line rationale blocks.
  - **generic-naming** — placeholder *def/class/param* names (`foo`/`bar`, `helper_2`),
    high-precision so ML names like `payload_v2` / `sha256` are spared.
  - **function-too-long / too-many-params / deep-nesting** — BLOCK above 88 body code
    lines, 6 required parameters, or 5 control-flow levels; an elif chain counts as
    one level, and flat data-shuttle / declarative bodies are spared.

Per-rule severity is config-overridable via `slop_severity.txt` (`rule=off|advise|block`,
`off` drops it), inheriting aislop's rule-severity model. `slop_allowlist.txt`
(`<path>:<line>:<rule>`) freezes today's findings — currently empty — so only *new*
slop surfaces.

Regenerate the slop seed after a deliberate change:

```bash
python3 tools/precommit/check_slop.py dump > tools/precommit/slop_allowlist.txt   # then re-add the header
```

See [CONTRIBUTING.md](../../CONTRIBUTING.md) for the change/check workflow and
[architecture](../../docs/architecture.md#dependency-rules) for dependency direction.
