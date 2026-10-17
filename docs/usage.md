# Usage guide

Everything beyond the [README](../README.md) Quickstart: first-run setup, the two ways to
run triage, how the model learns from your labels, offline use, the safety model, and the
full config reference. (Architecture and dev workflow live in [architecture.md](architecture.md).)

Search Add saves a planned key before native writing and reuses it after a
session-save failure. A plan is not delivery/library presence. New-item stamping
follows native return; coverage hits reuse keys. Same-process recovery is not
distributed or power-loss protection; #7 remains partial.

## Explicit qualified interpretation HTML

Private CODE callers can select the existing C qualifier and pass its **whole
transient packet** to the existing audited HTML publisher:

```python
from pathlib import Path
from tempfile import TemporaryDirectory
from zotero_summarizer.services.library._interpretation_view import current_review_view
from zotero_summarizer.services.library._paper_read_html import write_outputs

packet = current_review_view(item_key, interpretation_schema="interpretation-digest-response-v1")
if packet is None:
    raise ValueError("No current qualified review")
# Caller already owns/validates this paper directory and extracted content.
with TemporaryDirectory(dir=owned_paper_directory, prefix=".paper-build-") as temporary:
    outputs = write_outputs(owned_paper_directory, content, staging=Path(temporary),
                            interpretation_review=packet)
```

Do not pass separate non-None `digest`, `quality`, `goal_summaries` or `code_link`
arguments alongside the packet, even empty/equal ones: ambiguous mixing raises
before publication. All four projections and `interpretation_view` come from the
same packet; raw cache entries and loose views are not this API. Omitted or
`interpretation_review=None` keeps legacy rendering, call shape and return keys,
without optional imports or cache/model reads. A supplied packet is trusted CODE
input, not authentication or a currentness lease; obtain it freshly and do not
mutate or combine snapshots.

One Finding / Scope / Unproven section resolves closed references against that
packet's digest. Finding bodies live in Finding; basis values live in Scope;
Unproven retains references and complete uncertainty/extrapolation lists. Native
scientific paths, sentence parts and passage IDs remain inspectable, not invented
original quotations, pages, URLs or working anchors. Lists, repeats, nested values
and Unicode are retained and escaped. Available content visibly remains
AI-generated, scientifically unverified and a bound snapshot; contradiction
adjudication is not performed. Native support is not scientific/human/author
approval, causal warrant or whole-paper coverage.

**Native support references** is one closed-by-default native disclosure containing
all original associations, in order, without truncation or deduplication. The three
groups and both original safety paragraphs remain direct section children outside
it, including the AI/unverified, snapshot/currentness, contradiction, causal-warrant
and coverage limits. Only explicit trusted-code packet publication selects this
consumer; no public/default mode or new JavaScript/state is introduced.

A scoped native `::details-content` print-CSS candidate is included, but its browser
support and actual print/export retention are **UNVERIFIED**. Printing/exporting
initially closed HTML must retain the full trace before eventual acceptance; DOM
or CSS presence is not proof. Browser geometry, keyboard/focus, wrapping, light/dark
appearance and scientific benefit also remain unqualified.

Optional unavailability shows its reason and warning, keeps the base brief, and
cannot revive omitted raw interpretation/audit content. Malformed packets raise;
the existing blocking presentation audit still gates `paper_presentation.html`
and `paper_audit.json`. That audit checks extraction/artifact integrity, not
scientific correctness. Staging failures preserve prior outputs before replacement;
successful publication remains individually replaced files, not bundle-atomic.
This adds no default/public pipeline C build, renderer-cache identity, React/API/CLI
mode or consented native writing. Live-model, full-paper, native/editor and human
scientific-benefit acceptance remain separate, incomplete gates.

## Reading reviews and Zotero notes

Every ordinary or generated Pending Apply/retry requires its displayed common
`approval_preview` hash, binding ID, item/type, exact payload, edit revision and
native target. Editing (including saving identical text) retires old approval;
stale conflicts require inspecting and selecting the refreshed row, never an
automatic fresh-hash retry. Deploy API/frontend/MCP together: ID-only and old
inner-generated hashes are refused. Known Failed rows permit explicit current
approval/retry; Needs reconciliation does not. Ordinary Apply no longer removes
papers from Inbox implicitly: queue and approve an explicit collection removal.
The Apply receipt contains applied/failed counts, backup path and failed items,
not obsolete Inbox counters. Human acceptance of losing automatic cleanup remains
separate, as do live-native/browser acceptance and broader #7 writer coverage.
Reject stays ID-only. A completion from before a list refresh or unmount cannot
restore old selection/status or publish obsolete feedback; refreshed rows require
manual selection. A failure without a newer list still restores the same selection.

### Explicit human editors (backend candidate)

Tag edits, manual-priority changes and collection edits use their existing typed
request as deliberate consent, not a new Pending approval step. Their backend
candidate records positive action IDs, the normalized request/effective payload,
actual native target, actor/source audit and actual backup locator in the existing
SQL history. Success retains old fields plus `action_ids`, `backup_path` and
`delivery_status='applied'`. `updated` counts dispatched operations, not changed
membership rows; repeated collection references are not an exactly-once request ID.

Complete native failures return500 `zotero_write_failed` with the real applied/failed
partition. Unknown native/finalization outcomes return409 `zotero_write_uncertain`,
not a fresh write retry. Overlapping applying history blocks covered direct/Pending
writes before no-ops or backup, regardless of history-view limits; known other
items/actual targets remain independent. Missing/malformed recorded targets cannot
be silently rebound. After durable success, corpus/detail failure is separately500
`zotero_refresh_failed` retaining delivery evidence, never clean200 with a missing
item or an instruction to repeat native delivery merely to refresh.

Direct history always has `approval_preview:null`; Apply/retry/force/edit/reject/
status-reset cannot turn it into a proposal, including mixed selections. Generic
history/existence still sees it; ordinary queue production alone ignores validated
direct receipts when deduplicating exact signatures. A later explicit editor
request is new intent, not an acknowledged replay after response loss.

The locator is backup-only: no fabricated undo, attachment restore, reset or
automatic reconciliation. Existing native retention can expire older backups.
Terminal SQL commit acknowledgement loss after an already committed transaction
remains unproved; durable truth is not rewritten to promise always-applying or
exactly-once behavior. This is source awaiting guarded runtime and independent
review. Conspicuous history-only/no-action UI is deferred; current controls are not
backend authority. Other writer families, live-native/editor/real-library recovery,
human/scientific evaluation and full #7 remain separate open gates.

### Composite My notes (candidate)

System-mirror history separates native delivery from domain ACK. Matched applied
proof permits ACK-only continuation, never native replay or Pending authority.
Backup-only; runtime, recovery and same-intent native-library retarget remain open.

### Generated notes

Deep review no longer automatically writes generated digest notes to Zotero.
A published default library review stages an immutable proposal in **Ops → Pending**.
Inspect the full literal payload and target, then **Apply selected** to approve that
exact preview. Source/review changes (including changing away and back) require a new
proposal; `force` only addresses the Zotero-running guard, never consent. Applied
replays are no-ops. Rejection and delivery record actor/source audit events.
**Needs reconciliation** means external outcome is uncertain; no automatic retry is
allowed. Recovery is the recorded backup, not undo; older backups can expire under
existing retention. A queue failure leaves the published review readable and reports
a job error. No background writer upgrades an old approval. Feed/private-C reviews
remain non-writing; explicit Add, verdict and manual-note consent flows are unchanged.
This generated-note boundary does not certify every other write path or whole #7.

Full paper reviews lead with contribution and visible material caveats. Findings,
methods and limitations stay together; the rubric and secondary reference fields
are under **Full digest and assessment**. **Evidence** retains source quotes, and
**Original goal summaries** retains full supplied text. Abstained, unsupported and
unretrieved goals are different states. The other-goals disclosure has a neutral count;
unretrieved evidence is not a negative coverage finding. Manual-note app saves do not
confirm Zotero delivery or infer a recovery action from an unspecified mirror error.
Goal findings remain under their own disclosure;
shared caveat wording has one introduction with all distinct scope/context visible.
Saved source basis is explicit; checklist coverage is not reviewed-source extent.
Smaller cards keep their compact disclosure. Reading time, exact ordered targets,
useful skip targets and what the original adds sit beside the action, without
repeating in Full digest. Unrecorded legacy details stay unknown, not zero or
invented; skip/withheld advice cannot revive stale original-reading targets.

Expand **Idea / Evidence / Writing** for the saved novelty/significance and goal
state, independent contribution-type-aware scientific support, and concrete
exposition failures—not topic difficulty. High friction preserves an idea while
capping automatic advice at skim. Weak evidence separately warns against treating
concepts/hypotheses as established claims. Missing signals remain unknown. Full
reading needs explicit digest full-text provenance; abstract-only and unconfirmed
sources are distinct caps even if the quality assessment used full text. The
verdict editor remains the explicit human override/write boundary. These model
assessments do not prove read precision or idea-rescue recall; independent labelled
papers are still required.

Approved triage notes retain saved summaries across restarts and include available
methods, findings, relevance, uncertainty and optional implementation/impact sections.
A blank rationale falls back to a usable overview, not a generic placeholder. Full
paper-specific text and all saved findings, reading hints and tags are retained by
default, without a silent six-item limit. If an explicit renderer word budget is
used, oversized fields show an omission notice; consult the full saved summary rather
than treating that notice as evidence of absence.
Raw summary data and exact artifact URLs are retained. App-owned note footers also
contain visible ownership metadata: native editors discard comments, so removing
that footer loses same-note identification. Unrelated manual notes are not adopted.
The SQLite adapter stores notes in Zotero's native HTML envelope so headings and
lists remain HTML instead of being escaped as legacy plain text. Existing notes
are not automatically migrated. Ownership requires structural provenance, not an
ordinary paragraph mentioning a marker. Long sources reuse bounded mapping; only
explicitly truncated work is subdivided, never accepted as partial output. Notes
label the computed final priority separately when the model proposed another tier.
Native-editor round trips and human/proxy read/skip sufficiency remain separate
acceptance checks; passing preservation does not establish scientific correctness.

PDF triage admits method artifact URLs only from complete original text, bound
metadata or eligible whole original PDF hyperlink annotations. It retains literal
case, percent spelling and punctuation, never guesses links from wrapped text,
normalizes annotations or appends their raw tuple to prompts. One immutable
capture governs direct/map and first/repair; reader/hash/ordinary source changes
fail before model work. Rejection receipts bind full PDF and exact tuple hashes.
This is membership, not scientific relevance or usefulness. Live abstract receipt
writes remain required; Doctor/explicit dry is write-free and never reads a PDF.

PDF triage over 80,000 text characters supplements generated map notes with whole
original HTTP(S)-bearing ±200-character windows. Source-order selection merges
retained overlaps and skips windows that do not fit; matched/retained occurrence
counts disclose omitted context. The suffix budget is the existing
`quality_review.map_chunk_chars`, including escaping, policy and wrappers, measured
in Python characters—not tokens. Even custom refinement templates without
`{paper_text}` receive this separately escaped suffix after formatting. Direct PDF,
abstract/Doctor, shared library mapping and the distinct single JSON-repair prompt
remain unchanged; only initial long-source generation gains this context.
It adds no calls or artifacts automatically. URI windows exclude annotation-only
and URI-free evidence and do not establish contribution role, whole-paper absence,
scientific completeness or measured model-quality improvement.

Ask Paper uses deterministic metadata only for affirmative whole-paper figure-count
phrasing; scoped, uncertain, table-only, combined, or unmatched count questions fall
through to grounded Q&A. The paper artifact stores `figures_count`, not a table
count; focused Q&A and freshness routing tests passed (39), but live natural-language
scope accuracy is not measured.

Ask Paper processes the latest 20 history turns. The four recent turns retain
conversational text; older current-extraction handles retain complete identities,
original spans and repeated occurrences only when the checkpoint is strictly shorter.
Prior conversation remains escaped session data, not paper evidence. Question-only
input digests are neither authentication nor anonymization; see the
[checkpoint contract](../zotero_summarizer/services/library/README.md#qa-evidence-checkpoints).

Native Zotero annotation detail keeps highlighted `text` separate from literal
`comment` content, with additive `annotation_key`, parent `attachment_key` and raw
`date_modified`. Keys belong to the selected native user library/database, not a
global identity or authenticated author. Same-clock edits can change content, so
the clock alone is not a monotonic revision or sufficient cache invalidation.
This readonly metadata preserves legacy values/order/multiplicity and does not
add related-paper retrieval or interpret negative comments as interest.

Typed and preset Ask questions are saved before answering and remain separate
from editable **My notes**. The question list reports one composite Zotero-note
outcome (pending, acknowledged or not confirmed) with its reported revision;
new submissions or changed saved content retire the prior receipt and completion,
even if the same text returns. An already-acknowledged retry needs no new Zotero write.
Unreported delivery stays unknown. Comment-save, offline sync and QA outcomes
remain distinct; reconnect never automatically repeats QA. Compact cards open Ask
with one disclosure. Ask points to My notes for delivery instead of retaining an
older receipt. Navigating away retires in-flight manual-save feedback, even when
you return to the same paper before the response; a new visit's draft stays dirty.

### Accepted-digest evidence and local export

New deep reviews retain the existing verifier's accepted `tldr` and `key_findings`
checks in `deep_reviews.json`, separate from the model's digest schema. Standalone
`assess_digest`, `digest_for_strategy` and `map_reduce_digest` callers opt in with
an app-owned `verification_audit={}` sink; omitting it retains no audit. This adds
no verifier calls and changes no correction, publication, ranking or Add rules.
Accepted evidence contains original passages and exact character occurrences,
claim/source/digest hashes, extraction/verifier fingerprints, numeric-preflight
outcomes and correction/fallback lineage. Rank/prefix describe their actual
selected extraction; map notes are not evidence. Complete extraction does not
prove complete-paper coverage. Synthetic joins and unavailable PDF pages remain
explicitly unmapped.

In an already initialized local application/project context:

```python
from zotero_summarizer.services.library.deep_review import export_review_audit

result = export_review_audit(item_key)  # JSON-serializable value; no output-file write
```

A missing cache key returns `None`. Otherwise `audit_status` is `available`,
`stale` or `unavailable`, with `reason`; only an available audit includes evidence.
Legacy and missing-source entries never acquire invented checks. Current source
bytes are hashed without a size/mtime shortcut; extraction and verifier revisions
invalidate independently of generation identity. Export returns the stored digest,
not its current policy projection, and never calls a provider, repairs/quarantines
corrupt cache bytes, writes a file or publishes anything. Corrupt bytes/envelopes
raise `ValueError`; local read errors propagate. Saving the returned JSON is a
separate caller-authorized action.

`verified_digest`/its hash describe the accepted generator or correction result;
`cached_digest_sha256` binds the final policy-adjusted artifact.
`post_verification_changes` identifies changed fields, and each retained claim
reports whether its cached field still matches. Policy text is not silently
certified by the earlier verifier. A false support verdict means support was not
established, not contradiction; contradiction adjudication is not performed.
These local files contain source passages: do not share private material.
Scientific usefulness, five accepted-digest audits and the ten-paper expert pilot
remain separate acceptance work.

## First-run setup

There's nothing to copy and nothing to migrate by hand. On the first `serve` (or `setup`),
**Phase-0 bootstrap** runs: it writes a default `goals.yaml`, a minimal `.env` skeleton (a
*commented* secret placeholder + empty `PDF_ROOT` / `ZOTERO_DATA_DIR` lines — never a real
key), and migrates the triage DB if absent. It's idempotent — existing files are never
overwritten — so re-running is a no-op.

Then a **guided wizard** takes over. Two equivalent front ends, same `services/setup`
primitives underneath (they can't drift):

- **Web — the `/setup` wizard.** A brand-new install is redirected here once
  (skippable/resumable): **Connect Zotero** (auto-detects likely Zotero data dirs, picks the
  one whose `zotero.sqlite` exists) → **Choose AI-assisted or ML-only** (AI mode can run a live
  provider test) → **Describe research** (your goals). Empty-state "finish setup" cards on
  `/today` and `/library` link back here until you're ready.
- **Terminal — `zotero-summarizer setup`.** The same steps headless. Use `--mode hosted`,
  `--mode local`, or `--mode no-llm`; the hosted route is saved before its optional probe.

**Secrets stay server-side.** The web wizard may accept a key and stores it in the OS
keyring; status and config responses expose only redacted metadata. The terminal flow asks
for an env-var name (for example `OPENAI_API_KEY`) and reads the value from your environment.
ML-only mode stores no credential and deliberately skips AI Doctor checks.

AI-assisted setup can finish before the secret/endpoint is live — **Next** gates only on a
structurally-valid provider (type + base URL + key env-var name + model), not on a passing
connection test. Environment changes require a restart; keyring saves apply immediately.

HackerNoon can use existing RSS subscriptions and the practitioner rubric.
The non-shipped `tools.method_validation.practitioner` caller is a bounded
abstract diagnostic, not feed-daemon/selection acceptance. Frozen tag captures
contain zero entries; main-feed diagnostic stories are not substitutes.

Diagnostic receipts use started-call evidence for attempts. Generated tags cannot
mark successful calls as gate censorship or missing-abstract nonattempts. These
are machine accounting, not semantic judgments.

### Local setup verification boundary

The web wizard's Full local path hides API-key controls and blocks profiles below
the server-reported RAM/disk floors, including a previously saved incompatible
profile. Cards show runtime/model, approximate download size, source and the
manual pull command; copying a command never runs it. Existing endpoint users
can enter the model name before the runtime is reachable; the URL is under
Advanced. Model listing is inventory, not an inference pass.

After Finish, **Setup saved** opens the same Doctor checklist used by Settings and
CLI. All checks retain a text status: Not started, Running, Ready, Needs action or
Unavailable. Failed goals, Zotero, source and model checks open their existing
Settings controls, with a distinct Retry check after recovery. A failed health
read has Retry status; it never starts a model check. Successful verification is
required for Open Today, and running or failed status reads cannot unlock it.
The saved verification step survives reload/recovery navigation without restoring
an old wizard config over a newer Settings save. Interrupted local drafts revisit
hardware compatibility. A failed config-validation request saves nothing.
Saving setup, paths or credentials and adding/updating/deleting/importing sources
retire older health requests and reread the shared Doctor state. A late response
from verification of the previous configuration cannot restore Ready. Removing the
last source or adding its replacement clears prior verification; neither action
starts inference or substitutes for an explicit new check.
A missing or failed acknowledgement can follow an actual save. Uncertain config,
path and RSS write failures therefore retire cached health and passively read
current Doctor status; RSS rereads saved subscriptions too. The original error
stays visible, and neither the write nor verification is automatically retried.
Only traced prewrite 403/409/422 rejections retain valid health; other statuses,
including 404, do not prove rollback. Credential-save errors always retire health:
the server can invalidate it before credential validation. Model-list failures
alone do not retire health. No new model, download or source-fetch work starts.

Local inference can still use the network for RSS/PDFs. Offline-ready describes
cached/loadable model assets, not uncached papers. Strict offline additionally
disables optional enrichments and remote fallbacks. The health card keeps these
three states separate and folds startup diagnostics beneath the shared checklist.
UNKNOWN load safety is explicitly incomplete, not a prompt to retry until green.

Saving is configuration-only. Optional CLI real inference defaults to No.
Draft/Doctor/CLI use canonical endpoint locality, even when its name is `hosted`.
Each unique callback assesses the same captured provider/model/options before
construction; shared stages reuse one probe.

Local UNKNOWN refuses construction without blocking configuration, inventory or
separately consented downloads. Total-RAM/disk profile floors stay advisory.
No approved numeric budget/reserve, resident bypass or UNKNOWN exception is
provided. Verified Full-local completion remains blocked in software, not only
awaiting hardware tests: effective-envelope evidence is absent. Explicit local
setup requests now distinguish observed single-attempt completion from uncertain
work; missing/invalid receipts or abandonment still retain applicable HOLD.
These request receipts do not supply capacity admission or permit bypassing UNKNOWN.

Advanced operators may select `ZS_SETUP_LOAD_POLICY_PATH` and independently
configured `ZS_SETUP_LOAD_POLICY_SHA256`, both absent by default. The SHA binds
chosen bytes, not authenticated approval or measured sufficiency. There is no
policy editor or generated policy file.

The finite policy carries version, policy_ref, exact routing identity,
effective context/placement/memory domain and budget/reserve. Malformed presence
or read failure is an explicit error, even without a hash. Current clients do not
evidence an effective envelope, so production remains UNKNOWN.

Waiter timeout/cancel cannot unlock an active callback. Unproven request/retry
finality leaves sticky same-process HOLD after callback exit; this is uncertainty,
not evidence of ongoing work. Restart/unload is not request completion. There is
no reset or cross-process lifecycle guarantee. When every expected setup request
has a matching unique start and terminal response, the normal callback can finish
without inventing a daemon-idleness/unload requirement. SDK retries/cache and HTTP
redirects are disabled only for these observed local clients, and their connection
transport closes before callback ownership ends. `length` still fails readiness;
unknown/unfinished metadata and late completion after abandonment cannot clear HOLD.

“LLM verification needs action” covers local refusals and hosted abandonment
without claiming endpoint locality. The detail identifies the provider/model and
reason; Review endpoint settings only opens existing AI controls. Hosted
abandonment does not by itself create local HOLD.

Readiness binds policy/revision, not volatile RAM. HOLD or invalid current AI
configuration downgrades required load rows. Review endpoint settings only opens
existing AI controls (`/settings#ai-models` from the wizard); it never verifies,
saves, selects a model or grants load consent.

## Two ways to run triage

Scoring papers always runs the same pipeline (cheap ML gate → LLM for survivors →
SQLite). What *triggers* it is your choice:

- **UI, on demand (simplest).** Run `serve` and click **Triage backlog** on *Today*
  whenever you want fresh papers scored. No background process.
- **Daemon, automatic (optional).** `zotero-summarizer feeds serve` runs a background
  loop that scores unread items and can review top Today candidates in place. It
  never adds a candidate to Zotero automatically: open its full review, then explicitly
  Add it; Trash works immediately. The `feeds.*` block in `goals.yaml` only matters
  if you run the daemon.

The daemon is convenience, not a requirement.

### Local feedback evidence

`zotero-summarizer goldenset analyze-feedback --project-root ROOT` creates one
private descriptive JSON/Markdown report under that project's `data/feedback-analysis`.
An optional `--cutoff` is timezone-aware ISO time. The command does not start the
app, migrate source stores, contact providers or alter models/labels. It uses a clean
bounded child and prints a report/receipt pointer. No eligible support means an
`insufficient_data` report with explicit gaps, not a successful model evaluation.
Errors remain nonzero; a failure receipt's path is only an attempted locator.
Atomic no-replace publication currently requires Darwin; other platforms refuse.

Analytics recognizes exact `offline_sync` and `sync_conflict_resolution` sources
only on `label_transition`; their `human_feedback` remains nonpreference.
Raw provenance and separate channels are retained; existing reports are not rewritten.

Weekly automation is OFF unless `ZS_FEEDBACK_SCHEDULE_ENABLED=true` is explicitly
selected for `feeds serve`. It runs between ticks on the main thread: Monday00UTC
windows, oldest-first catchup bounded by `ZS_FEEDBACK_SCHEDULE_MAX_WINDOWS`
(default2/max4), with aggregate `ZS_FEEDBACK_SCHEDULE_WALL_CAP_SECONDS`
(default240/max600). Server startup, one-shot ticks and UI jobs do not activate it.
The private `schedule/cursor.json` points to the last positively finalized report;
failed writers, foreign/missing confirmation and interrupted work cannot advance it.
Changed same-window input creates a new full-content identity. Recovery of a genuinely
finalized cursor-write failure uses the exact frozen report rather than new analysis.

Installed-host/scored chronology/ranking and scientific usefulness remain unverified;
counts and confidence intervals describe selection-biased observed support only.
See [the operation/weekly contract](../zotero_summarizer/services/golden/feedback_analysis/README.md)
for source/ownership, resource, signal, no-fsync and cooperative-namespace limits.

## Adding sources (arXiv, bioRxiv, PubMed, HackerNoon)

A "source" is just an **RSS feed the app subscribes to itself** — the app fetches
and stores RSS/Atom in its own pool, it never reads Zotero's feed items. So any
public RSS/Atom URL is a first-class source, and the same pipeline (gate →
goal_sim → LLM → daily slate) scores all of them. To add one: **Settings → RSS
feeds** in the app, or `POST /api/rss/feeds`; `feeds list` shows it. Already have
feeds subscribed in Zotero? Import them once with the **"Import feeds from
Zotero"** button (`POST /api/rss/import-zotero`) instead of re-adding each URL.

Either way, Zotero's own feed cards still get marked read automatically once the
app has triaged the matching item (the read-sync reconciler) — so the unread
badge in Zotero stays clear even though Zotero isn't the source of truth anymore.

**PubMed** is the recommended source for *published, clinical* work (e.g.
medical agentic-AI papers) that the CS/bio preprint feeds miss. PubMed turns any
search into a feed: run a search → **Create RSS** → copy the URL → add it in
the app as above.

The query is your volume + quality filter; the daily slate only surfaces 1–2
papers regardless of intake. Two pitfalls found by validating these live against
NCBI:
- **`"agent"` mostly means a *pharmacological* agent** in PubMed — anchor it to AI
  (require an AI/LLM co-term), or even `"multi-agent"` matches *multi-agent
  chemotherapy*.
- **Use `[tiab]` free-text, not pure MeSH** — MeSH indexing lags: over the last 14
  days MeSH catches only ~39% of what title/abstract text does (it's ~87% over 6
  months). A feed wants the newest papers, so MeSH would miss most of them. The
  queries below use MeSH only where it adds recall.

Four validated feeds (counts = last 90 days; add as many as you want — DOI dedup
collapses overlap, the daily cap bounds volume):

```
# F1 — medical agentic-AI (~1.7/day, ~93% on-target)
(agentic[tiab] OR "agentic AI"[tiab] OR "AI agent"[tiab] OR "AI agents"[tiab]
 OR "LLM agent"[tiab] OR "LLM agents"[tiab] OR "autonomous agent"[tiab]
 OR "autonomous agents"[tiab] OR "multi-agent"[tiab] OR multiagent[tiab]
 OR "language model agent"[tiab] OR "language model agents"[tiab])
AND ("artificial intelligence"[tiab] OR AI[tiab] OR LLM[tiab] OR "large language model"[tiab]
 OR "large language models"[tiab] OR GPT[tiab] OR ChatGPT[tiab] OR "generative AI"[tiab]
 OR "foundation model"[tiab] OR "foundation models"[tiab] OR "language model"[tiab]
 OR "language models"[tiab])
AND (clinical[tiab] OR patient*[tiab] OR diagnos*[tiab] OR healthcare[tiab]
 OR medicine[tiab] OR "decision support"[tiab])
AND english[lang]

# F2 — clinical LLM applications (~12.7/day, ~87% on-target)
("large language model"[tiab] OR "large language models"[tiab]
 OR "generative artificial intelligence"[tiab] OR "generative AI"[tiab]
 OR ChatGPT[tiab] OR "GPT-4"[tiab] OR "foundation model"[tiab] OR "foundation models"[tiab])
AND ("clinical decision support"[tiab] OR diagnos*[tiab] OR "clinical workflow"[tiab]
 OR "electronic health record"[tiab] OR "electronic health records"[tiab]
 OR triage[tiab] OR "treatment planning"[tiab] OR "clinical reasoning"[tiab])
AND english[lang]

# F3 — oncology LLM/AI (~5.2/day, ~67% on-target)
("large language model"[tiab] OR "large language models"[tiab] OR LLM[tiab]
 OR "generative AI"[tiab] OR "generative artificial intelligence"[tiab]
 OR ChatGPT[tiab] OR "GPT-4"[tiab] OR agentic[tiab] OR "AI agent"[tiab]
 OR "AI agents"[tiab] OR "foundation model"[tiab] OR "foundation models"[tiab]
 OR "retrieval-augmented"[tiab])
AND (oncolog*[tiab] OR cancer[tiab] OR tumour*[tiab] OR tumor*[tiab]
 OR carcinoma[tiab] OR "Neoplasms"[Mesh])
AND english[lang]

# F4 — clinical decision support + LLM (~4.5/day, ~67% on-target)
("Decision Support Systems, Clinical"[Mesh] OR "clinical decision support"[tiab])
AND ("large language model"[tiab] OR "large language models"[tiab] OR LLM[tiab]
 OR "generative AI"[tiab] OR agentic[tiab] OR "Artificial Intelligence"[Mesh])
AND english[lang]
```

For an extra quality cut, AND a high-impact-venue clause, e.g.
`("NPJ Digit Med"[journal] OR "Lancet Digit Health"[journal] OR "Nat Med"[journal])`.
So these rank to the top of the slate, make sure your **research goals** (Settings)
include a clinical/agentic-AI sentence — goal-text similarity is the dominant
ranking signal.

**Full text.** When you read a PubMed pick, the app resolves a PDF via
`_pdf_acquire`: arXiv → Unpaywall/OpenAlex (by DOI) → **PMC** → browser. PMC matters
because the most on-target agentic papers (e.g. AMIA proceedings) sit in PMC with
*no DOI*, so the DOI rungs can't reach them. Note: fresh PMC papers have no reliable
headless download (the site serves a bot-wall page), so PMC full text needs the
**browser rung** — enable **Settings → University access** (the optional `[browser]`
extra). Without it, OA papers with a DOI still resolve headless via Unpaywall.

*Caveat:* PubMed RSS abstracts are often truncated to the conclusion (Zotero only
fetches the full record when you save an item to your library, not for feed
items). Triage runs on that partial abstract today; prestige + goal-text matching
still rank the picks well. If you find PubMed picks ranking poorly because the
abstract is too thin, that's the signal to add the deferred efetch backfill.

### HackerNoon (practitioner / engineering angle)

Add the working AI tag through the same **Settings → RSS feeds** form as every
other source:

```
https://hackernoon.com/tagged/ai/feed
```

They use generic RSS identity/dedup and the same rank/slate. Only the triage rubric
changes: concrete architecture, implementation, failures, measurements and reusable
steps score well; promotion, SEO and unsupported trend claims do not. When
web-article review is enabled, HTML-only articles use a bounded, text-only extraction
of the selected main document's body text—not a visual/page facsimile. Images, layout,
CSS-generated content, embedded frames, and shadow trees are outside that scope;
incomplete or over-budget extraction fails instead of clipping a prefix. Unavailable
declared scholarly PDFs are not substituted with paywall snapshots. Direct and authenticated
PDF downloads remain unchanged. See the [browser article memory boundary](browser-article-memory-boundary.md).

Operational caveat (verified 2026-08-29): HackerNoon documents tag RSS, but the
narrow `ai-agents` and `agentic-ai` paths return Cloudflare 403. The broader
official `ai` tag returns RSS and is the supported fallback; the practitioner
rubric filters its wider input. Refresh surfaces any later upstream error in the
feed row. Do not work around it with a source-specific scraper.

### Cover the flagship journal, not just the sub-journal

A common coverage gap: subscribing to a *sub*-journal (Nature Cancer, NEJM AI,
Lancet Digital Health, JAMA Oncology) but not its **flagship parent** (Nature,
NEJM, The Lancet, JAMA). Landmark cross-cutting papers — e.g. *"Towards autonomous
medical AI agents"* (Nature, 2026) — publish in the flagship and silently fall
through: there's no preprint, and PubMed indexing lags days–weeks, so nothing
ingests them in time. Subscribe the flagships too (verified RSS):

```
https://www.nature.com/nature.rss                                    # flagship Nature
https://www.nature.com/ncomms.rss                                    # Nature Communications
https://www.nature.com/natbiomedeng.rss                             # Nature Biomedical Engineering
https://www.science.org/action/showFeed?type=etoc&feed=rss&jc=science # Science
https://www.nejm.org/action/showFeed?type=etoc&feed=rss&jc=nejm       # NEJM
https://www.thelancet.com/rssfeed/lancet_current.xml                 # The Lancet
https://www.cell.com/cell/current.rss                                # Cell
```

The PubMed feeds (F1–F4 above) are the backstop — they catch flagship papers once
PubMed indexes them — but the flagship RSS gives same-day coverage.

## Bring your own ground truth

The relevance model is trained on a **golden dataset that you build** — nothing ships
with the repo. You create it just by using the app:

1. **Cull and label.** Add/Trash on *Today* and the fine labels (`must` / `should` /
   `could` / `don't`) in *Annotate* are recorded as engagement signals. You can also
   signal directly in Zotero with emoji tags: 🧠 deeply engaged, 👀 skimmed, 👎 not relevant.
2. **Refresh labels** (Settings tab, or `zotero-summarizer goldenset export`) writes
   `data/zotero-summarizer-golden.csv` from those signals — that file *is* your ground truth.
3. **Retrain model** (Settings, or `zotero-summarizer goldenset train-classifier`) rebuilds
   the relevance gate on your labels.

Until you have labels, the gate stays off and the app simply LLM-scores everything —
fully usable while your dataset grows. A few dozen keep/trash decisions is enough to
start; quality improves as you label more.

## Offline / air-gapped use

The app is **local-first**: the UI, Zotero I/O, the ML relevance gate, and Library
search (BM25 + embeddings + cross-encoder rerank) all run on your machine. There is no
telemetry and the served UI pulls no external assets (no CDN/fonts). Model
weights and hosted inference require a network unless prefetched or replaced
with a local model. New RSS items and uncached PDFs also require connectivity;
strict offline mode can work only with previously acquired paper content:

1. **The ML models** download from Hugging Face on first use (see the [README hardware
   table](../README.md#requirements) for sizes). Pre-cache them once while online:

   ```bash
   uv run zotero-summarizer prefetch-models          # download what's needed
   uv run zotero-summarizer prefetch-models --check   # report what's cached, no download
   ```

   Then run with cache-only model loading (skips the Hugging Face hub check, so a
   disconnected machine never hangs on a network timeout):

   ```bash
   ZS_OFFLINE=1 uv run zotero-summarizer serve        # or set ZS_OFFLINE=1 / HF_HUB_OFFLINE=1 in .env
   ```

2. **The LLM** (summaries, deep review, quality review) needs an OpenAI-compatible
   endpoint. Point it at a **local** server — Ollama, vLLM, LM Studio, `mlx_lm.server` —
   in `goals.yaml` / `.env` for local inference. This alone is **not** strict offline:
   prefetch ML assets and set `ZS_OFFLINE=1` for cache-only model loading; uncached
   RSS/PDF acquisition still needs network. With **no** LLM at all, summaries,
   deep review and Q&A are unavailable; the **ML-only "Triage backlog"** drain
   (the classifier gate) still scores your feed, and Library search/ranking is unaffected.

The optional enrichments — OpenAlex prestige, Unpaywall, arXiv full-text fetch — are
**off by default** and skip gracefully when offline.

### Disconnected browser working set

This is separate from offline **Mac inference** above. The PWA can retain a
previously synced working set while the app server is stopped:

1. While connected, choose **Sync now** in the header. Check the cached-paper
   count and last app sync. Open full reviews you want retained; otherwise the
   synced context is compact and explicitly labelled as possibly abbreviated.
2. Expand **Offline use & installation**. Confirm that the offline shell is
   active, then use the browser's Install action when available. Test reload
   with the server off before relying on it. Keep this origin's browser data:
   clearing site data deletes unsynced changes.
3. Reopen **Library** while disconnected. Read cached context and explicitly save
   verdicts or My notes. **Saved on this device** is not a server/Zotero receipt.
   PDF/figures, AI execution, meaning search, acquisition and rescoring require
   the Mac; they are not part of the synced working set.
4. Restart the Mac app at the **same address/origin**, then choose **Sync now**.
   Browser connectivity alone does not establish that the Mac is available.
   For **Changed elsewhere**, compare Server / This device and choose **Keep
   mine** or **Use server**. Both choices create an auditable resolution mutation;
   they do not silently overwrite one side. Rejected drafts remain copyable.

The current server deliberately accepts **same-machine loopback only**.
`make iphone` reports unsupported mobile deployment and exits without starting a
server or advertising a LAN URL. For this Mac, use `make api` and open
`http://127.0.0.1:8000`; see the [deployment boundary](local-deployment-boundary.md)
for the separate mobile requirements.
The Mac's localhost is not reachable from iPhone/iPad; do not expose a LAN port or
bypass origin/auth/TLS checks to work around this. Remote use requires a separately
supported authenticated HTTPS deployment and physical-device trust/installation
verification. Safari's Share → Add to Home Screen is installation guidance, not
proof that this deployment is remotely usable. Long-term iOS storage retention
and several weeks of real reading/conflict measurements remain unverified.

### Deep review on MLX (fast, on-demand)

The `deep_review` stage (the full-text digest + quality) sends ~16k-token prompts.
On ollama those prefill in ~3 min/call; `mlx_lm.server` does the same in ~16 s. So
`goals.yaml` routes **`deep_review` → the local MLX provider** (`:8080`) and keeps the
high-volume **`feed`/`backlog` → ollama** (`:11434`) — the feed daemon never depends
on MLX being up.

The 35B (~20 GB) is **on-demand + memory-gated** so it can't blow the 48 GB box:

```bash
tools/mlx-deep-review.sh   # frees ollama, refuses if RAM is tight, caps the KV
                          # cache (PROMPT_CACHE_BYTES=4G), then serves the 35B at :8080
```

It aborts with a clear message unless ~26 GiB is available (tunable: `MLX_MIN_FREE_GB`),
so it never swaps the machine. While MLX is down, the deep-review panel shows an
"unreachable" banner (`GET /api/admin/llm-reachability`) and triage keeps running on
ollama. Run it foreground (Ctrl-C stops it); single-instance.

**Prewarm on launch.** By default the app background-computes the top-5 not-yet-cached
deep reviews on startup (`quality_review.prewarm_on_startup_k`, env override
`ZS_DEEP_REVIEW_PREWARM_K`; `0` disables), so the **first** open is instant, not just the
second. It runs on the same single-flight job as the "Run deeper review" button — serial
on the local MLX (RAM-safe), parallel only on a remote provider — and skips papers that
already have a cached review, so re-launches are cheap. With MLX down it simply logs the
provider as unreachable and warms nothing.

## Safety model

Triage never writes directly to Zotero. It queues pending tag / note / collection
changes; you review and explicitly **apply or reject** them in the UI. Applying takes a
Zotero SQLite backup first. The app reads Zotero's local SQLite DB directly — it never
logs into anything on your behalf.

## Configuration reference

Two files under the project root, both gitignored and created for you on first run (see
[First-run setup](#first-run-setup)). The split of who owns what:

- **`.env` = optional environment-backed secrets + the two Zotero paths the app manages.**
  The web wizard uses the OS keyring; the setup surfaces write `PDF_ROOT` /
  `ZOTERO_DATA_DIR` here for you.
- **`goals.yaml` = app-authored; don't hand-edit.** Edit research goals + LLM routing in the
  **Settings** page; the app serializes the file. (Hand-edits are tolerated but the app is the
  writer of record.)

### Settings page — Essentials vs Advanced

Settings is chunked to keep the common path short:

- **Essentials (always visible):** research goals, triage criteria, AI on/off, the default LLM provider,
  Zotero paths — plus a readiness strip (Zotero · LLM · Goals · Model).
- **Advanced (one collapsible disclosure):** full stage routing, classifier gate (sub-fields
  appear only when the gate is enabled), corpus.

The legacy `llm.draft_model / refine_model / api_base / api_key_env` inputs were **removed**
from the UI — they duplicated the LLM-routing editor. The backend still auto-migrates an old
`llm` block in `goals.yaml`, so existing configs keep working. The LLM API secret is
redacted on every response; a key entered in the web form goes directly to the server-side
OS keyring.

### The guarded `.env` path writer + restart banner

When the wizard or Settings writes a Zotero path, it goes through an **allowlisted,
validated** writer: only `PDF_ROOT` / `ZOTERO_DATA_DIR` may be set, the path must exist
(otherwise the write is rejected), and every other line in `.env` is preserved byte-for-byte
(your secret is never touched). Path changes are read at process start, so after a write the
UI shows a **restart banner** — restart `serve` to apply.

### Setup HTTP endpoints (`/api/setup/*`)

The wizard is backed by a small read-mostly contract you can also hit directly:

| Endpoint | What it does |
|---|---|
| `GET /api/setup/status` | one readiness probe across config / LLM (provider, key-*presence* bool, advisory reachability) / paths / Zotero / trained classifier, with a `ready` gate |
| `GET /api/setup/detect-zotero` | read-only per-OS probe for likely Zotero data dirs (DB-present first) |
| `PUT /api/setup/paths` | the allowlisted `.env` path writer described above (422 on a bad/non-allowlisted key) |
| `POST /api/setup/validate-config` | dry-run config validation + an optional provider connection probe; persists nothing |

No setup response ever contains a raw secret — `api_key_env` is always just the env-var
**name**, and key state is a presence boolean.

### `.env` — secrets, paths, runtime knobs

You set the **secret** rows by hand; the **path** rows are written for you by the wizard /
`setup` CLI (you can still edit them directly).

| Key | Required? | What it is |
|---|---|---|
| `OPENAI_API_KEY` | yes (the one secret) | API key for the primary LLM. This is the value the wizard collects *by name* — you set it here yourself. (Use whatever name your provider profile's `api_key_env` points at.) |
| `OPENAI_API_BASE` | no | Optional OpenAI-compatible base URL when your provider profile references `${OPENAI_API_BASE}`; otherwise the base URL lives in the provider profile in `goals.yaml` |
| `PDF_ROOT` | app-managed | Your Zotero PDF storage, e.g. `~/Zotero/storage` — written by the setup flow; blank → defaults to your home dir |
| `ZOTERO_DATA_DIR` | app-managed | Your Zotero data dir, e.g. `~/Zotero` — written by the setup flow; blank → defaults to `~/Zotero` |
| `CUSTOM_BASE_URL` / `CUSTOM_API_KEY` | no | Optional second provider for the *Today* "Triage backlog" drain (a stronger model for the freshest papers). Leave blank to skip |
| `SUMMARY_TIMEOUT_SECONDS` | no | Per-request OpenAI-compatible LLM timeout and whole-triage summary deadline (default 420) |
| `TRIAGE_JOB_CONCURRENCY` | no | Parallel triage jobs (default 4, max 16) |
| `ZS_OFFLINE` | no | `1` → cache-only model loading (offline) |
| `ZS_DEEP_REVIEW_PREWARM_K` | no | Top-N deep reviews to background-prewarm on launch (overrides `quality_review.prewarm_on_startup_k`, default 5; `0` disables) |
| `APP_LOG_LEVEL` / `APP_LOG_FILE` | no | Logging |

### `goals.yaml` — research goals, models, prompts

**App-authored — edit it through Settings, not by hand.** First run writes a valid default
with placeholder research goals; you replace them in the **`/setup` wizard**, the `setup`
CLI, or the Settings **Essentials** panel. The parts a new user cares about:

```yaml
research_goals:                 # 1–6 free-form lines; what the model optimizes for
  - Multiagent systems in clinical and scientific research
  - Multimodal AI for clinics
triage_criteria: [ ... ]        # the evaluation rubric (ships with sensible defaults)
llm_routing:                    # provider profiles + per-stage model routing
  ...                           # edit in Settings → Advanced; the secret is name-only
                                # (api_key_env names an env var; the value lives in .env)
```

The default provider and per-stage routing are managed in Settings (Advanced → stage
routing). A legacy top-level `llm:` block in older configs is **auto-migrated** into
`llm_routing` on load — its UI inputs were removed (they duplicated the routing editor).

Other blocks (`corpus`, `prestige`, `feeds`, `quality_review`, `classifier_gate`, …)
have working defaults — leave them until you need them. `quality_review.shadow_claim_check`
(default off) opts into the local MiniCheck encoder claim-checker; see
[services/model/README.md](../zotero_summarizer/services/model/README.md).

All app state (the two SQLite DBs, your golden dataset, logs, ML artifacts) lives under
`data/` (gitignored).

## Faithbench QA builder coverage

`faithbench build` examines source text in contiguous, non-overlapping 6,000-character
windows. The default is 32 windows per paper (192,000 source characters); the maximum
is 256 (1,536,000 characters). Set the per-build limit with either the CLI option or
the builder-specific environment variable:

```bash
uv run zotero-summarizer faithbench build --max-builder-windows 64
ZS_FAITHBENCH_QA_MAX_BUILDER_WINDOWS=64 uv run zotero-summarizer faithbench build
```

Precedence is explicit `--max-builder-windows` > shell
`ZS_FAITHBENCH_QA_MAX_BUILDER_WINDOWS` > the selected project's `.env` value for
that key > default (32). The CLI validates the setting before loading project settings
or constructing a provider. Before the first builder inference call, the build preflights every selected
paper; if any paper exceeds the configured per-paper budget, the build fails without
making builder calls. The effective limit is stored in benchmark metadata. Each
additional window adds a builder call, so a higher limit costs more. This bound
ensures contiguous source coverage only for admitted inputs; it does not prove QA
semantic accuracy. Generated QA and traps still require human review and approval.

## Weekly Research Intelligence

Open **Weekly** to browse retained RSS digests, inspect cards and selection metadata,
follow paper-review links, and download canonical JSON or Markdown. A bookmarked
`/weekly?run=weekly-...` opens that immutable run. Unsupported bundles remain
unavailable; **Refresh saved runs** retries reading, not generation.

**Build from saved reviews** uses inclusive UTC dates, optional venue and at most
10 cards. It calls the existing weekly runner with cached reviews only: no new AI,
PDF acquisition or Zotero writes. Missing current reviews are not proof of missing
PDFs. The page requires the local server and does not cache reports on this device.
A disconnected build may still have published; refresh retained runs before retrying.

For explicitly consented generation, use the same CLI service:

```bash
uv run zotero-summarizer research-feed run --from 2026-08-20T00:00:00Z --to 2026-08-29T23:59:59Z --card-budget 10
```

That command may acquire papers/use the configured AI provider; add `--cached-only`
to avoid generation. Zotero tag changes are queued only with explicit
`--queue-zotero`, without `--dry-run`, and still require approval. A successful-stage
cursor can reuse unchanged canonical assessments/cards after fresh source/review
qualification, but ranking still uses the whole current cohort before budgets.
It is not a concurrency/exactly-once guarantee or evidence of human usefulness.
See the [RSS contracts](../zotero_summarizer/services/research_feed/README.md) and
[remaining evaluation inputs](issue-evidence-research-feed.md).

## Offline saved Telegram news

This optional CLI mode consumes an explicit saved JSONL export; it does not connect
to Telegram or enable a feed by default. Start with import/export only:

```bash
uv run zotero-summarizer research-feed run --source telegram --input-jsonl POSTS.jsonl --collect-only --project-root ROOT
```

Use your selected project root. See the [saved-news contract](../zotero_summarizer/services/research_feed/README.md#offline-saved-telegram-news)
for the version-1 post schema and existing profile interests. Omitting
`--collect-only` explicitly permits configured reranker initialization and default
LLM processing of the original text/interests; obtain content/provider consent
first. Loading failures and exhausted waits stay error/pending, not negative labels.
The configured wait does not cancel a download or bound total command shutdown.
`--cached-only` projects retained results without new model calls; `research-feed
view --report SAVED.md` reads a retained report without reconstruction. Telegram
never queues Zotero writes. Live collection, calibration and usefulness remain open.

## Command reference

```bash
uv run zotero-summarizer serve            # FastAPI server + browser UI (auto-bootstraps goals.yaml/.env + migrates on first run)
uv run zotero-summarizer setup            # headless guided onboarding (Zotero dir, LLM provider, research goals)
uv run zotero-summarizer migrate          # init / upgrade the local SQLite stores (serve does this for you; here for re-runs)
uv run zotero-summarizer mcp              # MCP server over stdio (agent surface)
uv run zotero-summarizer smoke-test       # verify the app constructs
uv run zotero-summarizer prefetch-models  # download the ML models for offline use (--check = status only)

# Feeds (optional daemon / one-shots)
uv run zotero-summarizer feeds list                 # discover feed names + IDs
uv run zotero-summarizer feeds serve                # background feed triage (review before manual Add)
uv run zotero-summarizer feeds run --feeds "Agents" # one-shot: exhaust one feed
uv run zotero-summarizer feeds tick                 # single tick (cron/launchd-friendly)

# Ground-truth lifecycle
uv run zotero-summarizer goldenset export            # write data/zotero-summarizer-golden.csv
uv run zotero-summarizer goldenset train-classifier  # (re)train the relevance gate on your labels
```

## HackerNoon with the normal RSS workflow

Open **Settings → RSS feeds** and add HackerNoon AI Agents and/or Agentic AI.
The shortcuts preserve an existing disabled source and an unsaved custom draft.
Choose **Refresh RSS now** to see actual insert/update counts and source errors;
**Retry loading feeds** recovers an unavailable list. Offline skips and an
upstream feed containing no stories do not count as new stories. Open Today to
use the existing triage/ranking/review flow. Its default backlog drain is ML-only;
practitioner LLM triage runs in the existing LLM-enabled feed pipeline. No model
run, semantic usefulness, or nonempty live story is implied by adding a source.

On the shared paper-review page, offline verdict changes and retractions now
enter the existing durable device outbox immediately instead of pausing in the
query scheduler. A verdict already saved offline does not wait for a server-only
rejection tag. This core fix does not establish remote iOS deployment or all
Library offline navigation; those remain separate acceptance work.
