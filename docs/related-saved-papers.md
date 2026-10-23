# Related saved papers and retained comments

Open a paper from Today or Library in `/paper/:key`. **Related in my library →
Show related saved papers** searches saved items, including already-read items,
using the existing local metadata hybrid search. Up to three paper links appear.
Search similarity alone is not a verified relationship. Corpus/model unavailability
is distinct from a successful empty result; the paper review remains usable.
When all inspected comment sources are observed empty, the optional action returns
`no_comments` without model work. Unavailable empty sources instead return
`context_unavailable`; neither becomes a successful comment assessment.
Current explicit human `dont_read` verdicts exclude candidates. If the app context
cannot be read, retrieval and assessment stop with a source error instead of
treating unknown verdicts as eligible. Machine labels,
negative words, emoji and inferred sentiment do not become deterministic exclusions.
The API's optional `collection` and `tag` scopes are applied before retrieval;
unavailable Zotero-only scopes fail explicitly with the app-only reader.

## Optional private-context experiment

Metadata hybrid remains the default. **Try with comments — experimental** shows
the authoritative configured `deep_review` provider, endpoint and model, then asks
for confirmation. It sends complete retained seed **and candidate** comments to
that processing boundary, not to any literature-search provider. A changed routing
identity requires new consent. The shared runtime client cache binds admission
to an owned effective routing snapshot and thinking variant; invalidation advances
a generation so an older builder cannot repopulate the current cache. Metadata
checks alone are not treated as proof about an opaque cached client. The consent
token uses the shared `ResolvedStage.fingerprint()` and is passed as the runtime
resolver's `expected_identity`; a different actual snapshot rejects before cache
access or client construction, including an away-and-back config change.
Replacing a credential through Settings invalidates cached stage clients even
when routing metadata is unchanged. Reading the processing boundary does not build a client or read credentials. Opening the page,
loading related papers or refreshing context does not make this semantic model call. There is no external discovery,
automatic full-text review, Add, label, note, training or Zotero write in this flow.
The explicit assessment action does persist its own private session under
`Settings.search_dir` (`data/search/`); those files must not be shared publicly.
The shared directory is owner-only (0700), including pre-existing directories;
new/replaced session files are 0600, set before writing private context.

The experiment assesses a metadata-retrieved pool, not a comment index. The local
BM25/dense/cross-encoder retrieval query contains only seed title/abstract.
One serial invocation of the existing deep-review stage receives the complete
allowlisted metadata and retained context for up to ten candidates. The configured
client can perform parser or transport retries: one stage invocation does **not**
mean one HTTP request. Existing provider timeout/output limits remain in force.
No new model, encoder, store engine or background worker is introduced.

Work ceilings are named, validated environment overrides:

| Environment | Default | Allowed |
|---|---:|---:|
| `ZS_RELATED_CANDIDATES` | 10 | 1–10 |
| `ZS_RELATED_INPUT_CHARS` | 30000 | 1000–30000 |

The input limit includes escaped records, instructions and schema. An oversized
context returns `budget_exceeded` without a semantic call or note clipping; the
metadata results remain available. It is a character/work ceiling, not a tokenizer
or process-memory guarantee. An unavailable comment source stays failed/unknown,
never an observed empty read; partial context is explicitly labelled.

The assessment considers negation/criticism in full RU/EN context, not a bag of
positive-interest keywords. A candidate is recommended only when typed relevance
is `relevant` **and** preference compatibility is `compatible`. Negative, unknown,
missing, duplicate or malformed identities cannot become positive recommendations.
This is still model judgment, not a tested guarantee that the model understands
negation. Supplied IDs record assembly; model-cited IDs record claimed use, not
proof of semantic influence. `comments_in_ranking` reports model-cited context
use only; `submitted_context_ids` separately records actual assembly. If the model
cites none, the response does not claim comment use merely because it received text.

## Source and result ownership

- Current app notes come from `review_notes.current`, including unambiguous
  feed/materialized aliases, monotonic revisions and authoritative deletions.
  Human verdict comments retain their original item key and revision. Machine
  verdicts are not represented as human opinion.
- Native notes retain note keys, and annotation comments retain annotation and
  attachment keys and native modification times. Materialized aliases supply
  their native comments even when the Today seed uses the app reader. Authorship is uncertain. Highlighted paper text is separate
  from the user's annotation comment. Short and negative notes are not filtered.
- Owned generated triage/digest notes and app-note/verdict mirrors are excluded
  using structural ownership, not substring matching. Deleting an app note does
  not resurrect its old native mirror. Generated cached deep reviews are not
  treated as user comments or paper evidence; this path never calls the cache's
  read/quarantine/review-generation helpers.
- Recommended connections require nonempty, verbatim metadata quotes identifying
  the seed and candidate, field and offsets. Used comment IDs are attached
  separately. Quote membership and identity are mechanical checks, **not**
  entailment proof. Abstract/title-only judgments cannot establish full-text
  confirmation or contradiction.

The existing Search session store saves the seed and complete assessed-paper
context, snapshot/fingerprint, processing identity, claim and terminal result.
Unassessed library metadata/app context contributes its complete digest to
eligibility/invalidation, not its raw note bodies to each new session. This keeps
same-count edits, revisions and deletion sensitivity without another store.
Existing session files are retained unchanged. Repeated identical clicks share
work in the single app process, including failed results. After interruption or
failure, an explicit consented retry retains the prior attempt. No automatic retry
spends model work. Source/corpus fingerprints, encoder/model/configuration,
prompt/schema, scopes, and both sides' full note bodies/revisions/deletions affect
identity. Changes during assessment return `stale`, not a current positive result.
Corrupt session/schema reads fail loudly. Results use atomic file replacement,
not a multi-process claim or fsync/power-loss guarantee. These sessions are omitted
from the Search sidebar and never enter its automatic review flow.

## Saved-context display ownership

An acknowledged seed note/verdict save fences the shared panel immediately,
before its detail refetch returns. A `dont_read` verdict's primary acknowledgement
fences it even while the secondary reject-tag request is pending. Secondary failure
still reports the saved-verdict warning; other completion/collapse callbacks retain
their existing timing. Changed saved detail also remounts it with a
new React-owned query key. Old cached or pending reads cannot become that owner's current
comments, even when the paper key is unchanged. Closed/loading/error states hide
retained comment bodies; GET cancellation is carried through the existing API
wrapper. Explicit Show/Refresh/Retry performs a fresh read without an automatic
semantic POST. Failure remains an error, not observed empty. Metadata search,
complete comments, consent and the server's context fingerprint remain the
same contract; no second store or semantic worker is introduced.

## Qualification boundary

This implements an **optional contextual reranking experiment over a bounded
metadata-retrieved saved pool**, not all of issue #46. Comment-only candidate recall,
external “Find new relevant”, and owner-labelled usefulness/latency qualification
remain outstanding. Before changing the default, compare embeddings-only, existing
hybrid and hybrid-plus-comments on 10–20 owner-labelled seeds. Report top-three
useful matches, explanation support and latency, including mean/median and
breakdowns with model/provider/config provenance. Synthetic fixtures do not
establish any semantic improvement or human acceptance.

`tests/test_related_saved_papers.py` exercises HTTP assembly, real SQLite note
identity/deletions, the production hybrid input, consent, unknown/conflict gating,
quote/ID rejection, persisted claims/results, coalescing, budgets and invalidation.
`tests/test_zotero_read_items.py` additionally exercises native annotation keys,
parent attachment identity, same-count edits and deletion through the real reader.
`frontend/src/components/paper/RelatedPapers.test.jsx` covers the explicit consent
and truthful state surface. Scripted model judgments check the machine contract,
not live negation understanding, relationship entailment or usefulness.
`tests/test_related_client_consent.py` retains the actual config-swap/cache race
oracle. `tests/test_runtime_stage_client_cache.py` adds effective-settings,
thinking, clean reuse, unchanged-routing invalidation, away-and-back consent
binding and failure controls.
`RelatedPapers.stale.test.jsx` retains same-item edit/delete/late-GET regressions
and adds explicit-load recovery and read-failure controls. The page-level
`PaperReviewPage.related-context.test.jsx` exercises the actual note/verdict save
callbacks while holding the detail refetch pending. `PaperReviewPage.reject-ack.test.jsx`
holds both secondary tag completion and detail refresh after a primary rejection
verdict acknowledgement; `usePaperReview.ack.test.jsx` preserves separate primary,
secondary-warning, completion, failure and delete callbacks.
