# tools/precommit — repo guardrail checks

Guardrail scripts wired by `.pre-commit-config.yaml` (stdlib-only, except
`check_dead_code.py` which also shells to Vulture, a dev dependency).

**Two ways to use them.** Enforcement (only *new*, un-grandfathered findings fail) runs
automatically on commit and in CI via `pre-commit run --all-files` — or `make lint`. To
*see* findings (every detector, advisory, never blocks), there are exactly **two commands**,
differing only in scope:

```bash
make scan         # every detector across the WHOLE tree (the full backlog)
make scan-diff    # the same, scoped to the .py you changed vs the base branch (fast)
# knobs: EMBED=1 adds the semantic code-model overlap pass · BASE=<branch> (default main)
```

| script | enforces |
|---|---|
| `check_file_loc.py` | `.py` ≤ 500 LOC; legacy files frozen at their `loc_allowlist.txt` ceiling |
| `check_import_policy.py` | the layered-import rules + "new service modules go in a domain subpackage" |
| `check_module_readme.py` | every package has a README; editing a package's code re-stages its README |
| `check_dead_code.py` | dead-code identification (two tiers, below); existing findings grandfathered |
| `check_redundancy.py` | redundant-transform + near-duplicate identification (Tier 6, below); existing findings grandfathered |
| `check_slop.py` | AI-slop / dead-code patterns (Tier 7, below); existing findings grandfathered |
| `check_overlaps.py` | all-pairs semantic overlap audit (run via `make scan`/`make scan-diff`, advisory — **not** a hook) |
| `check_allowlists.py` | `reconcile`: flags STALE grandfathers (committed key with no live finding) — run in `make scan` + the test suite |
| `loc_allowlist.txt` | grandfathered oversized files (path + frozen ceiling) — shrink to empty |
| `dead_code_allowlist.txt` | grandfathered orphan public symbols (`<path>:<symbol>`) — shrink to empty |
| `vulture_allowlist.txt` | grandfathered Vulture findings, **path-anchored** (`<path>:<name>`) — shrink to empty |
| `redundancy_allowlist.txt` | grandfathered redundancy findings (transforms + clone pairs) — shrink to empty |
| `slop_allowlist.txt` | grandfathered slop findings (`<path>:<line>:<rule>`) — shrink to empty |
| `slop_severity.txt` | optional per-rule severity overrides (`rule=off\|advise\|block`) — empty by default |

## Dead-code identification (`check_dead_code.py`)

Two complementary tiers, each grandfathering today's findings so only *new* dead
code blocks:

- **`consumer-check`** (stdlib AST + `git grep`) — a public, top-level,
  *undecorated* function/class with no reference inside `zotero_summarizer/`
  beyond its own definition is an orphan. Robust to dynamic registration: a
  handler passed to `router.add_api_route("/p", h)`, a CLI handler in
  `set_defaults(func=_h)`, and an `__all__ = ["Name"]` entry all appear in the
  grep → counted as consumers. `@mcp.tool()`-decorated symbols are skipped.
  Orphans are frozen in `dead_code_allowlist.txt`. **Tests do not count as
  consumers** (the grep is runtime-only) — a `# test-only` helper still lands in
  the allowlist by design; that's a signal it isn't reached by any runtime path.
- **`vulture-sweep`** (Vulture, a dev dep — **pinned exact** so findings don't
  drift between versions) — catches what name-grep can't: unused imports/locals,
  unreachable code, unused private methods. Findings ≥ 80% confidence block;
  60–79% are advisory. Current findings are frozen in `vulture_allowlist.txt` by
  a **path-anchored** `<path>:<name>` key, so a name acknowledged in one module
  never masks a genuinely-dead symbol of the same name elsewhere (the old
  bare-name whitelist's collision hole), and the key survives line drift.
  Three false-positive classes are killed **at the source** rather than
  per-symbol: `--ignore-decorators` (`@mcp.tool`/`@mcp.resource`, pydantic
  `@field_validator`/`@model_validator`, FastAPI `@app.*`/`@router.*` — all
  registered with a runtime the grep can't see); `--ignore-names "row_factory"`
  (the sqlite3 attribute its C layer reads, never our code); and a **structural
  guard** (`model_field_keys`) that auto-suppresses pydantic `BaseModel` /
  `@dataclass` field declarations (framework-serialised, never read by name) —
  advisory-only, so it can never hide a ≥ 80% blocker. The **whole ignore policy
  lives in `vulture_argv`** (one source of truth); `make scan` calls
  `vulture-scan` and the regenerator calls `make-allowlist` rather than re-typing
  the flags, so they can't drift from the gate.

Both allowlists follow the `loc_allowlist.txt` rule: entries may be **removed**
(wire a real consumer, or delete the symbol) but a new entry needs a one-line
justification. `check_allowlists.py reconcile` flags any entry whose target no
longer produces a live finding (a stale grandfather), so they can only shrink.
Goal: shrink to empty.

Regenerate the seed files after a deliberate change (the subcommands source the
ignore policy from `vulture_argv`, so the flags live in exactly one place):

```bash
python3 tools/precommit/check_dead_code.py dump-orphans  > tools/precommit/dead_code_allowlist.txt   # then re-add the header
python3 tools/precommit/check_dead_code.py make-allowlist > tools/precommit/vulture_allowlist.txt     # then re-add the header
python3 tools/precommit/check_allowlists.py reconcile     # verify no stale grandfathers remain
```

## Redundancy identification — Tier 6 (`check_redundancy.py`)

Catches code an LLM (or a tired human) writes that does the same work twice. The
**prime directive is soundness**: a flag must be a *behaviour-preserving* redundancy,
so a false positive can never pressure a behaviour-changing "simplification". Where
redundancy can't be proven from the AST alone, the finding is **advisory** (printed,
exit-neutral), never blocking. Two subcommands, mirroring the dead-code split:

- **`transforms-check`** (file-based, **BLOCKs** new findings) — one AST walk over
  staged runtime files flagging redundant transforms derived from *semantic invariants*
  (not a snippet catalogue, so un-enumerated compositions still collapse):
  - **idempotent self-application** `f(f(x))` for builtins whose return type is a
    guaranteed fixpoint — `sorted/set/frozenset/list/tuple/dict/str/int/float/bool`.
  - **faithful round-trips** `outer(inner(x))` where `inner ∈ {list, tuple}` (order- +
    multiplicity-preserving) and `outer ∈ {list, tuple, sorted}` (consumes the whole
    iterable, adds no precondition) — e.g. `sorted(list(x))`, `list(tuple(x))`.
  - **eager identity comprehensions** `[x for x in it]`, `{x for x in it}`,
    `{k: v for k, v in d.items()}`.
  - **involutions** `-(-x)`, `~~x`.

  Two firewalls keep it sound: the **simple-call guard** (a rule fires only on a single
  bare positional arg, no keywords/`*args`) so the two-key stable sort
  `sorted(sorted(rows, key=a), key=b)` is *never* flagged; and the **binding-scope
  guard** (a builtin name that is locally `def`'d, assigned, imported-as, or a parameter
  is skipped) so `def sorted(...)` / `import OrderedDict as dict` don't misfire.

- **Advisory** (printed by `transforms-check`, but **never** blocking) — transforms that
  are redundant only under input assumptions the AST can't verify: `dict(list(x))`
  (mapping-vs-pairs dispatch can `ValueError`), `set(sorted(x))`/`frozenset(sorted(x))`
  (orderability precondition can `TypeError`), `abs(abs(x))`/`round(round(x))` (custom
  `__abs__`/`__round__`), and the lazy `(x for x in it)` / `map(lambda x: x, it)`.

- **`clones-sweep`** (whole-tree, **advisory**, always exits 0) — surfaces near-duplicate
  functions. Each function gets an AST-normalized **structural fingerprint** (local names
  alpha-renamed to placeholders; free names, attributes and call targets kept) used to
  bucket candidates, then a pair is reported only if its **semantic-Jaccard** over the
  kept API tokens clears `0.75`. So two functions that share only a control-flow skeleton
  but call disjoint APIs are dropped — parallel interface implementations are legitimately
  similar, hence advisory. A sound `min/max` length pre-filter skips pairs too differently
  sized to reach the threshold before scoring. (The fingerprint's ordered token stream is
  exposed as `structural_tokens()` in `_redundancy_clones.py`; `structural_signature` hashes
  it for bucketing, and the on-demand overlap audit below consumes it for graded similarity.)

`redundancy_allowlist.txt` freezes today's findings (two key formats:
`<path>:<line>:<kind>` for blocking transforms, `<path>:<qualname>::<path>:<qualname>` for
clone pairs) so only *new* redundancy surfaces — same shrink-to-empty rule as the others.
It currently holds **0 blocking transforms** and the repo's pre-existing parallel-function
pairs.

Regenerate the redundancy seed after a deliberate change:

```bash
python3 tools/precommit/check_redundancy.py dump > tools/precommit/redundancy_allowlist.txt   # then re-add the header
```

## AI-slop identification — Tier 7 (`check_slop.py`)

Adopts the deterministic slop/dead-code detectors of [aislop](https://github.com/scanaislop/aislop)
("catch the slop AI agents leave behind") into this repo's stdlib pre-commit style. Same
discipline as the other tiers: each rule encodes a **principle** (a shape / task-class
invariant), every false positive is killed by a **guard** (not a special case), **AST is
preferred over text** (comments are read via `tokenize`, so a marker inside a string or
f-string never trips a comment rule), and a detector never silently no-ops. Two subcommands:

- **`slop-check`** (file-based, **BLOCKs**) — fires on the one *unambiguous* defect:
  **debug-leftover**, a committed `breakpoint()` / `pdb.set_trace()` / `ipdb.set_trace()`.
  Guards: `breakpoint` is skipped if shadowed (a domain `def breakpoint(...)`); imports
  are *not* treated as shadows (`import pdb` is how you reach the real debugger).
- **`slop-sweep`** (whole-tree, **advisory**, always exits 0) — everything heuristic, so it
  can never pressure a behaviour-changing edit:
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
  - **function-too-long / too-many-params / deep-nesting** — Long-Method complexity; an
    elif chain counts as one level, and flat data-shuttle / declarative bodies are spared.

Per-rule severity is config-overridable via `slop_severity.txt` (`rule=off|advise|block`,
`off` drops it), inheriting aislop's rule-severity model. `slop_allowlist.txt`
(`<path>:<line>:<rule>`) freezes today's findings — currently **0 blocking** and the repo's
pre-existing long functions / best-effort handlers — so only *new* slop surfaces.

Regenerate the slop seed after a deliberate change:

```bash
python3 tools/precommit/check_slop.py dump > tools/precommit/slop_allowlist.txt   # then re-add the header
```

## On-demand: function-overlap audit (advisory, not a tier) — `check_overlaps.py`

Tier 6's `clones-sweep` buckets functions by an **exact** structural hash, so it only ever
compares same-skeleton pairs and scores them on API-name overlap. This audit answers a
different question — *which functions across the whole tree overlap in **intent**, even when
the control-flow shape or the API differs?* — by comparing **every function against every
function** (all-pairs, whole-tree) and ranking them. It is **advisory**: it runs inside
`make scan` (whole tree) and `make scan-diff` (your changed functions vs the whole corpus,
via `--changed`); it is deliberately **not** a pre-commit hook, **not** in CI, **not** in
`make lint`, and **always exits 0**.

Each pair gets a hybrid score from three signals:

- **embed** — cosine over the function source from a local code-embedding model (the semantic
  signal that catches overlap the deterministic signals miss),
- **struct** — a graded `SequenceMatcher` ratio over the `structural_tokens` stream (works
  *across* different shapes, unlike the exact-hash bucket),
- **api** — the same semantic-Jaccard over the kept API vocabulary as Tier 6.

Combined `0.5*embed + 0.3*struct + 0.2*api` (hybrid), or `0.6*struct + 0.4*api` when no model
is available; default threshold `0.55` (recall-oriented, below the `0.75` clone bar), top 50.

The embedding backend (`_overlap_embed.py`) is the **only** importer of `sentence-transformers`
and is loaded lazily, so `check_overlaps.py` imports torch-free and the tests inject a fake
embedder. It reuses this repo's existing `sentence-transformers` dependency — default model
`jinaai/jina-embeddings-v2-base-code` (code-specialised; first run downloads ~320 MB to the HF
cache, then `ZS_OFFLINE=1 make scan EMBED=1` runs offline). The scans default to the fast
**deterministic** signals (no model, no network); `EMBED=1` opts into the semantic pass. When
the model is absent/uncached the audit **announces the reason on stderr and degrades** to the
two deterministic signals.

```bash
make scan                  # whole tree, deterministic overlap (fast, offline)
make scan EMBED=1          # whole tree, + semantic code-model overlap (downloads the model 1st run)
make scan-diff             # only your changed functions, deterministic, vs the whole corpus
make scan-diff EMBED=1 BASE=develop                                         # semantic, vs a different base
# direct CLI (power users):
python3 tools/precommit/check_overlaps.py audit --model sentence-transformers/all-MiniLM-L6-v2  # reuse the prefetched app model (offline)
python3 tools/precommit/check_overlaps.py audit --threshold 0.7 --top 0 --json
```

See [docs/architecture.md](../../docs/architecture.md) for the rules in context.
