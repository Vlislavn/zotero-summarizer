# Zotero Summarizer

A local-first reading assistant for [Zotero](https://www.zotero.org/). It reads the
RSS feeds you follow, scores each new paper for how worth-reading it is (a cheap ML
gate first, an LLM for the survivors), and gives you a small daily slate to cull. Your
keep/trash decisions train the model, so tomorrow's slate is sharper.

```
  app RSS pool (self-fetched) → [ML gate → LLM] → ranked daily slate → you cull / read / label
        ▲                                                                      │
        └──────────────── retrain on your labels ◄─────────────────────────────┘
        reviewed + explicitly added picks → Zotero Inbox · approved tag/note changes → Zotero (backup first)
```

**Local-first · no telemetry · trained on _your_ labels** (nothing ships with the repo —
the model learns from how you triage). The app owns current decisions and their history;
Zotero remains the PDF/citation surface and a synced representation of approved tags/notes.
Pending selections belong to the displayed list snapshot: an older Reject response
cannot reselect or overwrite refreshed rows. See the [write-review contract](docs/usage.md#reading-reviews-and-zotero-notes).

The explicit tag/manual-priority/collection backend candidate retains durable
history in the existing SQL queue, with positive action IDs, actual target/backup
and backup-only recovery. Direct receipts never authorize Pending actions; unknown
same-item delivery blocks covered writes even outside capped history views. A
refresh error does not erase native delivery. Implementation runtime/review and
history-specific UI acceptance remain pending: this is not undo, exactly-once or
whole #7 coverage. See [explicit editors](docs/usage.md#explicit-human-editors-backend-candidate).

Composite **My notes** records system-mirror history before native work and commits
native status before domain ACK. Exact applied proof permits ACK-only continuation,
not native replay or Pending approval. Source candidate only; runtime, recovery
and broader #7 gates remain open.

## Review recovery and search constraints

Deep-review caches retain accepted `tldr`/`key_findings` verifier evidence without
extra model calls. The local `deep_review.export_review_audit(item_key)` API returns
the exact stored digest and current audit, never policy projections or repairs.
Evidence includes extraction coordinates and checked scope, not a scientific verdict.
See [audit retention/export](docs/usage.md#accepted-digest-evidence-and-local-export).

Explicit programmatic HTML publication can consume one whole qualified current
interpretation-review packet, showing Finding / Scope / Unproven with resolved
same-snapshot values and AI-generated/unverified disclosures. **Native support
references** holds the complete trace in one closed native disclosure; the three
groups and all existing safety caveats stay outside it. Browser/keyboard/print
behavior and scientific benefit are not yet qualified. This private path
is not enabled by public/default builds, React or native notes; see the
[HTML packet contract](docs/usage.md#explicit-qualified-interpretation-html).

Deep review rejects demonstrated access/error/login templates before model calls.
Failures show their stage and recovery separately from scientific quality; a failed
attempt or missing-source placeholder does not replace a current valid review.
`verify-deep-review --capture-local` explicitly saves sensitive decoded diagnostics
under `data/`; do not share them. Public source, issues and PRs must not include
private infrastructure identities, personal paths, library screenshots or private
evaluation artifacts. Use portable examples and explicitly configured routes.
It performs no Zotero writes and disables thinking
for its diagnostic calls. See [library](zotero_summarizer/services/library/README.md)
and [CLI](zotero_summarizer/cli/README.md) contracts.

Full reviews keep contribution, caveats and findings together before folded assessment.
The shared Idea/Evidence/Writing disclosure separates idea value, type-aware support
and grounded exposition friction. Missing signals stay unknown; saved policy reasons
explain caps without another model call. Full reading needs explicit digest full-text
provenance, not the independent quality layer's source. Time, ordered read/skip targets
and original-only value stay beside the action, without folded duplicates.
Goal evidence remains accessible without repeated summaries; approved Zotero notes use
persisted paper-specific content. See [usage](docs/usage.md#reading-reviews-and-zotero-notes)
for optional long-field budgets and the separate native/user acceptance boundaries.
App-owned notes carry visible ownership metadata because native editors discard
HTML comments. Keep that metadata to retain same-note updates; unrelated manual
notes remain unowned. Note writes use Zotero's native HTML envelope; existing
notes are not automatically migrated. Refinement validates an unambiguous complete typed JSON root,
uses the canonical schema for its single repair, and preserves source outcome status
(observed versus simulated/projected) rather than presenting every result as measured.
Long-source mapping uses bounded work units with narrow truncation recovery; exhausted
segments still fail. Computed priorities are distinguished from the model's pre-mapping
proposal without changing numerical scoring. Marker-like text alone cannot own a note.
Saved findings, reading hints and tags are not silently list-capped. Independent
preservation/mutation tests run without private inputs. Frozen real-review browser
replays require `ZS_REVIEW_ACCEPTANCE_MANIFEST=/path/to/frozen-input.json`; without
that explicit input they skip, not pass. They check literal retention, not scientific
truth, subjective usability or native Zotero persistence.

PDF triage binds method artifact membership to complete original text, bound
metadata and eligible exact hyperlink-annotation URIs from one immutable capture.
Raw annotations are never appended to prompts or reconstructed from wrapped text;
reader/source-identity failures are errors, not empty authority. Membership alone
does not establish scientific contribution or usefulness. See the
[triage contract](zotero_summarizer/services/triage/README.md).

Successful model-scored abstract-only summaries expose the legacy
`should_deep_read` field as `"No"` without changing the refinement used for triage
or full-PDF responses.
Existing stored summaries are not rewritten. This is an output compatibility
boundary, not a full-text reading decision or usefulness assessment.

When web-article review is enabled, HTML-only pages can be reviewed from a bounded,
text-only extraction of the selected main document's body text—not a page facsimile.
Images, layout, CSS-generated content, embedded frames, and shadow trees are outside
that scope; incomplete or over-budget extraction fails rather than clipping a prefix.
An unavailable declared scholarly PDF is not replaced by a paywall snapshot. See the
[browser article memory boundary](docs/browser-article-memory-boundary.md).

Search's folded explicit constraints are user-owned. Model proposals are visible,
not hard gates; inferred refinement exclusions cannot silently remove results.
Confirmed publication types require source metadata, with bounded exact-DOI recovery;
unconfirmed metadata stays unknown. Legacy saved plans retain their original filters.
The plan discloses domain context, source accounting and scholarly coverage limits.
With questions, explicitly select **Promote evidence-backed answers (experimental)**
to check bounded original-text passages before review cutoffs and promote sufficient
located answers. Feed generator and Deep review verifier routes must differ;
missing prerequisites preserve baseline order. Completeness and unchecked text stay
visible. Default rollout remains off; human quality/latency gates are unmeasured.
The supplied-original shadow remains available separately. See the
[Search evidence boundary](zotero_summarizer/services/search/README.md).

HackerNoon RSS uses the existing reader and exact-host practitioner rubric, with
shared identity/scoring. The [diagnostic caller](tools/method_validation/README.md)
checks parser and prompt IO only; frozen empty tag feeds, selection, web review and
semantic usefulness remain separate evidence.

The explicit [offline saved-news mode](zotero_summarizer/services/research_feed/README.md#offline-saved-telegram-news)
accepts saved Telegram JSONL, not live collection. Processing may use the configured
reranker/default LLM; collect/cached/view modes avoid new model calls. No default
activation, calibrated hard gate or scientific-usefulness claim is implied.
accepts saved Telegram JSONL independently of Telegram. Explicit
`research-feed telegram-auth` prepares a bounded one-time terminal login with your
own application identity; `research-feed run --source telegram` collects the
profile's public-channel allowlist in a dedicated macOS CPython child, then reuses
saved-news processing after verified clean child exit. The optional `telegram`
extra is required only for live/auth modes. Collect/cached/view avoid model calls;
processing uses the configured reranker/default LLM. No default activation,
calibrated hard gate, content permission or scientific-usefulness claim is implied.
See the [CLI boundary](zotero_summarizer/cli/README.md#explicit-telegram-auth-and-live-collection).

Ask saves typed/preset questions before answering. **My notes** keeps them
separate from manual text and owns the revision-bound composite Zotero delivery
status; Ask does not retain a stale second receipt. Compact cards open Ask in one
disclosure. New submissions or changed saved content invalidate old receipts.
Missing delivery evidence remains unknown. See [usage](docs/usage.md#reading-reviews-and-zotero-notes).
The opt-in synthetic checks in `tests/test_review_question_add_browser.py` cover
explicit Add, repeat/recovery and Library/restart preservation against an owned
native SQLite fixture. Supplied reviews are source-bound test input, not generated
or verified output; these checks do not establish live-Zotero, device or scientific acceptance.

### Disconnected browser working set

After a successful app sync, Library can reopen cached papers and compact review
context with the server off. Explicit verdict/note saves stay on this device until
sync; the header offers retry and side-by-side conflict resolution. Both Library
full-review shortcuts open cached pages without preparing PDFs offline. Confirmed
Remove proposals distinguish a device save from a secondary Zotero-tag warning.
See [disconnected browser use](docs/usage.md#disconnected-browser-working-set).
The server remains loopback-only: authenticated HTTPS deployment and physical
iPhone/iPad installation/trust are not yet delivered by this PWA workflow.
`make iphone` exits with that limitation; it does not start a LAN server. For use
on this Mac, run `make api` and open `http://127.0.0.1:8000`. See the
[deployment boundary](docs/local-deployment-boundary.md) for mobile requirements.

## Requirements

- **Python 3.10+** and **[uv](https://docs.astral.sh/uv/getting-started/installation/)**
- At least one **RSS feed** added in the app (arXiv, bioRxiv, or a **PubMed** saved
  search — see [docs/usage.md](docs/usage.md) "Adding sources").
- Optional: **Zotero desktop** for PDFs/citations and approved Zotero writeback;
  existing Zotero feed subscriptions can be imported in one click.
- Optional: an **OpenAI-compatible LLM endpoint** — **local** (Ollama, vLLM,
  LM Studio, `mlx_lm.server`) or **hosted** (any API). ML-only mode needs none.
- **Node 20.19+** to build the browser UI from a clean checkout.

**Hardware** — the app itself is light; the only heavy part is the **optional local LLM**:

| You run… | Need | What you get |
|---|---|---|
| **Hosted API, or no LLM** | ~8 GB RAM · any modern CPU · **no GPU** | ML triage + Library search run on-device; a hosted API adds summaries / brief / ask with **no local-LLM RAM** |
| **A local ~7–20B LLM** | 16–32 GB unified RAM (Apple Silicon) or an NVIDIA GPU | local inference for summaries, briefs and Q&A; RSS/PDF acquisition may still need network |
| **A local ~35B LLM** | 48 GB+ unified memory, or 24 GB+ VRAM | highest-quality deep reviews + quality grading |

The on-device ML (relevance gate + search) runs on **CPU** — no GPU required for the app.
**Disk:** ~1.5 GB of ML models (downloaded once) plus your data under `data/`. The LLM is
**optional**: with none at all, the ML-only "Triage backlog" still ranks your feed.

## Quickstart

```bash
# 1. Install (uv creates the env and installs from the lockfile)
uv sync

# 2. Build the browser UI (frontend/dist is generated, not committed)
cd frontend && npm ci && npm run build && cd ..

# 3. Run — first launch auto-creates goals.yaml + a .env skeleton and migrates the DB
uv run zotero-summarizer serve
```

First run bootstraps everything and the in-app **`/setup` wizard** walks you through the
rest — no manual file copying:

```
install ─▶ build UI ─▶ serve ─▶ /setup wizard ─▶ Today
                               Connect Zotero
                               Choose AI or ML-only
                               Describe research
```

Open <http://127.0.0.1:8000/>. A brand-new install lands on the **`/setup` wizard**
(choose Full local / Hosted / No LLM → Connect Zotero → choose a model if needed → Describe research) with Zotero-path
auto-detect and an optional live LLM connection test. The web wizard can store an entered
API key in the OS keyring and never returns it to the browser. The headless CLI asks only for
an env-var name and saves the chosen routing before its optional probe. Use
`setup --mode no-llm` when you want classifier-only triage without an endpoint.

Local setup verification now refuses construction when policy/effective-envelope
evidence is UNKNOWN; hardware floors are static advice, not load adequacy.
Explicit local setup requests can establish request-only completion without a
false unconditional HOLD; cancellation/uncertainty and admission guards remain.
This is not daemon-idleness, model-unload or hardware acceptance evidence.
Configuration and explicitly consented downloads remain available. The wizard
shows the exact remaining checks, resource floors and download size/source before
any manual command. Settings has one Local setup health card with scoped retry
and links to the existing recovery controls. Saving or listing a model never
counts as verified inference. Setup/source saves retire older health responses, so
late verification cannot restore Ready for a changed configuration. See the
[setup boundary](docs/usage.md#local-setup-verification-boundary).
A failed save response is not proof that nothing changed. When a setup/source
write's outcome is uncertain, the existing health card passively rereads status;
RSS also rereads saved rows. The original error remains visible, with no automatic
verification, source refresh, model work or write retry. Known prewrite rejections
retain valid health; credential-save failures always retire it.

After setup, go to **Today** and click **Triage backlog**
to score your unread feed papers, then start culling. *(Going offline? Run `uv run
zotero-summarizer prefetch-models` once while online — see [docs/usage.md](docs/usage.md).)*

## What you'll do

- **Today — cull.** A ranked slate of fresh feed papers. Generate and inspect a full-text
  review before explicitly **Add to library** (materialized into the Zotero *Inbox*);
  **Trash** remains immediate without a review. Both decisions train the gate.
- **Library — read.** Your unread papers, ranked by relevance. For each you get:
  - a **paper brief** — Read / Skim / Digest is enough with estimated time, exact read/skip
    targets and what the original adds (unrecorded details stay unknown), goal-match
    board, reference-free **quality grade** (FLAG / NEUTRAL / HIGHLIGHT), and figures;
  - **ask the paper** — grounded Q&A that quotes the text and abstains when the answer isn't there;
    follow-ups preserve complete older evidence handles when compaction saves space
    ([checkpoint rules](zotero_summarizer/services/library/README.md#qa-evidence-checkpoints));
  - **deep review** — an on-demand full-text digest + quality assessment for your top picks;
    map-reduce verifies claims against the original paper, not its generated notes.
    Empty source text or chunk summaries fail rather than becoming a skip recommendation.
- **Weekly — connect papers to projects.** Browse retained engineering digests, artifacts,
  reproduction gaps and project ideas, or explicitly build from saved RSS/reviews.
  The web build starts no AI, download or Zotero write; unknowns stay unknown.
- **Annotate — label.** When you actually read one, give it the fine label
  (`must` / `should` / `could` / `don't`). That's your ground truth; the model retrains on it.

Open PDFs and take notes in Zotero as usual; come back here to triage.
Native annotation detail retains `annotation_key`, parent `attachment_key` and raw
`date_modified` alongside separate highlight/comment text. Keys are library/database-
scoped; the clock is not a monotonic revision or sufficient cache invalidation.
This readonly metadata does not add comment-aware retrieval or infer authorship.

Long PDF triage (>80,000 text characters) supplements map notes with bounded,
escaped original URI windows and occurrence-coverage counts. Initial refinement
receives them after default/custom templates, even overrides without `{paper_text}`;
direct PDF, abstract/Doctor, shared mapping and distinct JSON repair stay unchanged.
No extra calls or automatic artifacts: context delivery is not scientific accuracy
or whole-paper completeness. See [the source limits](docs/usage.md#reading-reviews-and-zotero-notes).

### Related saved work and explicit discovery

Open a full review from Today or Library to see **Related in my library**: up to
three existing-hybrid matches, including already-read items. Expand Evidence for
literal title/abstract offsets and Comments for current app/native provenance.
Metadata overlap is not confirmation, contradiction or full-text verification.
Comments are context-only, not ranking signals. Positive reading verdicts and
token fit cannot qualify the meaning of mixed/negative comments. The existing
interfaces do not supply that semantic qualification; comment-aware ranking and
its owner-labelled usefulness comparison remain incomplete.

Saved seeds offer **Find new relevant**. This explicitly sends only the paper's
title/abstract to the existing configured Search pipeline, excludes known saved
identifier families, and persists a discovery-only session. Nothing is added or
automatically reviewed. Unknown membership/coverage remains unknown; “new” does
not mean recently published. Discovery distinguishes observed empty sources,
provider failures and unknown outcomes; the same action retries failed snapshots.
Offline related snapshots are labelled stale. Identifier-only discovery results
open supported DOI/PMID/OpenAlex links; deleting an old search session cannot
abort unrelated discovery or invalidate its persisted retry replacement.
See [the library contract](zotero_summarizer/services/library/README.md#related-saved-work).

## Configuration

Two files under your project root, both gitignored and **created automatically on first
run** — no templates to copy:

| File | You touch | Managed by |
|---|---|---|
| `.env` | optional CLI-managed API-key environment values | the app writes the two Zotero paths here via the `/setup` wizard / `setup` CLI; the web wizard stores pasted keys in the OS keyring |
| `goals.yaml` | nothing by hand | app-authored — edit research goals + LLM routing in **Settings**, don't hand-edit |

The Settings page is split into **Essentials** (research goals, triage criteria, the default
AI on/off, LLM provider, Zotero paths — always visible) and a collapsible **Advanced** section (full
stage routing, classifier gate, corpus). Secrets stay **name-only** everywhere in the UI: it
collects the env-var name, never the raw value. Everything else has working defaults. Full
reference in [docs/usage.md](docs/usage.md).

## Commands

```bash
uv run zotero-summarizer serve            # FastAPI server + browser UI (auto-bootstraps on first run)
uv run zotero-summarizer setup            # headless guided onboarding (same flow as the /setup wizard)
uv run zotero-summarizer doctor           # verify the real configured pipeline
uv run zotero-summarizer calibrate        # optional measured runtime calibration
uv run zotero-summarizer migrate          # init / upgrade the local databases (serve does this for you)
uv run zotero-summarizer prefetch-models  # download ML models for offline use (--check = status)
uv run zotero-summarizer feeds serve      # optional feed triage and in-place slate reviews (no automatic Zotero Add)
uv run zotero-summarizer goldenset train-classifier  # retrain the relevance gate on your labels
uv run zotero-summarizer faithbench build --max-builder-windows 64  # larger QA-source budget; more builder calls
```

## Going further

- **[docs/usage.md](docs/usage.md)** — the daemon, how the model learns from your labels,
  offline / air-gapped use, the safety model, and the full config reference. Faithbench QA
  builds cover contiguous 6,000-character windows (32 per paper by default); its CLI/env
  budget and increased call cost are documented there.
- **[Research-feed evaluation evidence](docs/issue-evidence-research-feed.md)** — why the current
  30-paper fixture is unscorable for production inclusion quality (not a 0%-precision result).
- **[Feedback evidence](zotero_summarizer/services/golden/feedback_analysis/README.md)** — `goldenset analyze-feedback --project-root ROOT`: private JSON/Markdown from an isolated bounded child; default-off weekly automation only in `feeds serve`. Darwin publication is supported; installed-host/scored evaluation remains unverified. Exact `offline_sync`/`sync_conflict_resolution` sources are recognized only on `label_transition`; their `human_feedback` remains nonpreference, and existing reports are not rewritten.
- **[docs/architecture.md](docs/architecture.md)** — how it works, the layering rules, and
  the dev / verification workflow.
- **[RepoDoc experiment](experiments/repodoc/README.md)** — seed-bound Python-backend KG,
  failed caller-impact and three failed parser/resolver controls from completed diagnostics.
  Costs are narrowly scoped, not whole-system evidence. Parent-only acceptance; no dependency or CI integration.
- **[Native issue delegation](docs/native-agent-workflow.md)** — pinned async Pi adapter,
  exclusive workers, independent review, per-child cwd/role checks, isolated failure
  handling, authority boundaries and restart/activation checks.
- **[CHANGELOG.md](CHANGELOG.md)** — notable changes (latest: the guided first-run setup).

### Add HackerNoon sources

In **Settings → RSS feeds**, use the explicit HackerNoon AI Agents / Agentic AI
shortcuts, then **Refresh RSS now**. Existing or disabled subscriptions and your
custom feed draft are preserved. Source errors, never-checked state, offline
skips and genuinely empty feeds remain distinct. Today uses the existing pool;
its default backlog drain is ML-only, while the existing LLM-enabled feed path
uses the practitioner rubric. An empty upstream feed is not a usefulness result.

On the shared paper-review page, offline verdict changes and retractions now
enter the existing durable device outbox immediately instead of pausing in the
query scheduler. A verdict already saved offline does not wait for a server-only
rejection tag. This core fix does not establish remote iOS deployment or all
Library offline navigation; those remain separate acceptance work.

### Today selection diagnostics

Today labels **Relevance: moderate** instead of Fair, independently of AI review
evidence. **Why selected** replaces the reason-chip row and shows the existing
selection policy, observed picker/cohort keys, saved priority and quality origin.
Missing composite values stay unscored; a review join miss never establishes a
failed, stale or absent review. Interleaved selections withhold new diagnostics.
Scores, order and defaults are unchanged. The actual consecutive-30 displayed /
rejected-sample diagnosis and held-out human usefulness comparison remain open.
See [the Today contract](zotero_summarizer/services/triage/daily_select/README.md).
