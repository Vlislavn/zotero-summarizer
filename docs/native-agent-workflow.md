# Native issue delegation

```text
parent → worker(async) → receipt → terminal result + independent readback
                   → saved review.js → scout → failure | two reviewers
appointed coordinator → native FANOUT_CHILD → scout/reviewer grandchildren
ordinary leaf → no delegation
```

This is an adapter over `pi-subagents`, not a new runner. The leading parent owns
scope, issue grouping, integration and closure. Each implementation assignment
has an exclusive worker and an independent reviewer.

## Installation and current acceptance boundary

Project `.pi/settings.json` pins **`npm:pi-subagents@0.76.1`**, MIT, npm gitHead
`7d072b91dca7e1aa915282f0159cec0175d65efa`. The npm tarball and installed lockfile
match the published SHA-512 integrity. Pi CLI and its package manifest report
**1.1.0**, despite the old Homebrew directory name. Installation used the normal
project-scoped command with dependency lifecycle scripts disabled:

```bash
pi install -l --approve npm:pi-subagents@0.76.1
```

On 2026-10-10, `npm audit --json` in `.pi/npm/` reported **0 advisories**. This is
an installed-tree audit, not a universal vulnerability or runtime-quality claim.
Runtime dependencies and run state are ignored by Git.

The project filter disables the old `npm:@piex-dev/subagent` extension without
changing global settings or `@piex-dev/goal`. Installation does not prove that an
already-running owner loaded the package. **Only if the owner still exposes the
old blocking adapter**, exit that process and resume the same session from this
checkout; do not start a second writer or edit the session JSONL:

```bash
pi --session <current-session-ID>
```

A core update requires process restart, not `/reload`. Before delegation, verify
one active `subagent`, use `subagents_enable({})` if the loader is offered, then
native `action:"list"` and `action:"validate"`. Those checks do not launch children.
Do not substitute a CLI/tmux runner if activation or infrastructure fails.

## Assignment and authority

Every dispatch must contain the complete current assignment, or a required
immutable brief the child reads fully: absolute allowed root, version-bound
Inputs/Read, exclusive Edit or read-only scope, current safety/method/project
instructions, Checks, expected artifact/refusal and escalation route. Automatic
project/global instruction inheritance and the skill catalog are disabled;
`fresh` alone is not an authority or privacy boundary.

| Profile | Tools | Descendants |
|---|---|---|
| scout/reviewer | read, grep, find, ls | none |
| worker | read, bash, edit, write, grep, find, ls | none |
| coordinator | read-only plus native delegation/wait/supervisor | assigned scout/reviewer only |

Workers are forbidden by their assignment to mutate Git/index/branch/STATE,
stash, commit, push, switch models, start alternate runners or modify unassigned
data/processes; the profile does not technically prevent those actions. `acceptanceRole:
writer` is inference, not proof of host checks. Coordinator depth remains bounded
at two; ordinary leaves cannot inherit delegation or release authority. These
assignments are **not a filesystem sandbox**. Private inputs require separate
consent before transmission to an external provider. The parent must obtain it
before dispatch, including before placing private content in a child's task.

Child profiles use **openai-codex/gpt-6.1-sol / xhigh**, preserving this project's
standing agreement. Parent/global model and thinking settings are unchanged.
Check effective model/thinking and actual terminal metadata before acceptance;
a different project's historical model agreement is not permission here.

Preflight agent discovery at **every actual child cwd**, not only the workflow
root. A worktree without these project profiles/settings can resolve the same
`worker` name to a builtin with different tools/thinking. Source-only assignments
may use the verified governed cwd and absolute read-only source roots; do not
silently substitute a role or hot-switch a retained child's contract. Bound role
files/settings, then check the actual launched model/effort. Stopped or invalid
contracts require an explicit, labeled recovery and preserved partial evidence.

## Native calls after restart

```js
subagent({ agent: "worker", context: "fresh", async: true,
  task: "<complete version-bound assignment or required immutable brief>" });
subagent({ action: "validate", workflow: ".agents/workflows/review.js" });
subagent({ workflow: ".agents/workflows/review.js", async: true,
  args: { task: "<complete current read-only review assignment>" } });
```

The saved script is plain JavaScript. Scout transport failure stops review;
otherwise awaited `runs.all` returns two independent results in input order, not
a keyed map. `ok`/`transportComplete` describe transport, not a positive review.
The parent reads verdicts and verifies artifacts independently. For independent
lanes, reuse `runs.lanes`: a failed/stopped stage stays blocked without cancelling
healthy siblings. A rejecting `runs.run` promise is not a fulfilled `ok:false`
receipt; bare `Promise.all` can fail the whole composite. Pass actual owner
`outputReference` values to later reviewers, never guessed output paths. The saved
review workflow requests complete scout-artifact readback when a reference is
available, otherwise passes inline output, and labels it untrusted evidence.
This framing is not an enforced prompt-injection defense. Proposal-only owners
need read-only tools and an explicitly appropriate evidence policy,
not an implementation attestation; peer/parent acceptance remains mandatory.
A per-call `tools` value is not proof of an enforced tool ceiling: in the observed
source-only worker run, mutation tools remained exposed. Task permissions still
forbid their use; prefer a configured read-only profile when a hard tool surface
is required. Verify actual exposure and never advertise a sandbox from mock checks.
Inline `output`
can include a saved-path notice: verify literal artifact content through the
returned `outputReference`, not equality against transport display text.
The saved `smoke.js` uses literal canaries for machine wiring only; it does not
establish model quality, writer/recursive safety or product acceptance. Both saved
workflows label stages without changing their stable keys.

Offline machine contracts execute the actual saved JavaScript using synthetic
native API receipts; no children, provider calls or private inputs are involved:

```bash
node --test tests/native_workflows.test.mjs
```

These cover task rejection, awaited scout completion, rejection/nonterminal stop,
ordered fresh review requests, artifact-reference handoff, six labels, literal
canary request payloads, preserved peer failures and smoke sequencing.
They do not establish effective runtime permissions, live provider behavior or
semantic correctness. Use native validation and actual terminal/artifact readback
for the separately authorized live wiring check.

After an async receipt, continue independent work or return control for native
completion wake. Do not sleep/status-poll. Retain actual runId/asyncDir/result
references; never guess paths. An appointed coordinator uses native `bg_wait`
for its own children, then reads terminal results. Supervisor requests belong to
the immediate parent: A's reply to B does not answer C's request to B.

A failure requires exact run/root/branch/ref/dependency state and retained partial
diff. Unknown process cleanup is a stop, not permission for another writer.
Resource preflight, owned process-terminal proof, required outputs, host Checks,
fresh review and owner acceptance remain separate gates. Syntax/configuration
checks do not prove provider/auth, live execution, semantics or issue completion.

## Reference and simplicity gate

**Close:** Codex retains child identity, lineage and status separately from spawn;
message enqueue is not observed completion. Source opened:
`codex-rs/core/src/tools/handlers/multi_agents_v2/spawn.rs:120-200` and
`send_message.rs:34-48` in the Codex reference checkout.
**Diverge:** reuse the pinned native Pi package, not Codex's control API.
**Defer:** private-history forks, unrestricted recursion and live acceptance until
the correct owner runtime is active. Package source/docs, not recalled APIs,
define these calls.

UX source: primary public Laws of UX (Hick, Tesler, Occam). Subtraction first:
remove duplicate `subagent`, mandatory CLI/tmux recovery and automatic context
inheritance; reuse one package and ordered review workflow. Keep failure, pending
state and authority boundaries visible. No new launcher or alternate product
surface is introduced.
