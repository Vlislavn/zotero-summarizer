## What this lets you do
<One plain sentence: user action and useful result.>

## Real feature smoke — <PASS / FAIL / NOT VERIFIED>
**Environment:** <production / isolated real app>; <revision>; <run time>.
<If isolated: Not tested in production. Name synthetic inputs or substituted dependencies.>

| What I actually did | Intended result | Observed result |
|---|---|---|
| <Concrete action/input through the real feature entrypoint> | <Specific behavior/state> | <Actual visible output/state; no invented success> |
| <One important failure/no-op boundary> | <Specific safe behavior> | <Actual observed result> |

**Evidence:** <Decisive short response/log/state excerpt + trace/screenshot/run link when available.>
**Repeat:** <1–3 short actions or an exact shipped CLI/HTTP command, including starting state.>
**Blockers / not verified:** <Real remaining risks and uncovered behavior; none only when justified.>

<details><summary>Automated checks — not the feature smoke</summary>
<Actual relevant commands and results, briefly.>
</details>

<Preserve exact honest issue/dependency references.>
