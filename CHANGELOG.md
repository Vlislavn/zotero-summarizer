# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Current behavior lives in [docs/features.md](docs/features.md); history only.

## [Unreleased]

- Long papers in Triage (#284): a remote provider reads the whole paper in one call (its window, as deep review); longer sources (>80k chars on local) map chunks (parallel on remote, serial on local; same map step as deep review), and a cancelling job says «Stopping after the current AI call finishes…».
- RSS refresh counts (#285): a story served by several feeds keeps its first feed instead of flipping on every fetch, and GitHub release notes no longer change per fetch (expiring image tokens dropped), so a repeat refresh reports 0 updated; «unchanged» now counts items, not feeds.
- Settings connections (#281): «Connect provider»/«Load models» on an address and authorization that is already saved reuses that connection (no more duplicates); provider status re-checks after a save instead of showing a stale «✓ Provider reachable»; a briefly busy settings file waits ≤2 s, then says «Settings are saving — try again in a moment».
- Slow server vs. absent server (#282): a request timeout or 502/503/504 now shows «Server is busy — retrying…» and keeps retrying; «Server unreachable» when the connection is refused, the device is offline, or 3 timeouts in a row get no answer; Today no longer says «AI not connected» after a failed or timed-out status check.
- Plain wording (#277): digest reproducibility rationale (page and Markdown), search «What was searched» source names («OpenAlex · keyword search»), and Pending apply result («6 changes applied.»).
- Search clarity (#274): «Reviewed N of 10» while reviewing, plain per-source «What was searched» lines (results / not available right now / «Some sources failed»), HTML entities and heading breaks in abstracts and titles, one «Published article · Crossref» chip.
- Find new relevant no longer fails with «Seed context changed; reload discovery» (#275): the discovery fingerprint ignores the constantly written `corpus_cache.db` file marker and changes only with the seed's own data.
- Busy database and Ask (#269): a locked database answers 503 «The app was busy; try again in a moment.» (quiet retry ×2) instead of a 500; Ask on a paper without a PDF fails at once with plain wording and no Retry; triage errors read «The model did not answer in time» / «The database was busy; run again»; editors say «The app is busy» (not «need the Mac») when the server merely errored.
- Plain-words fixes (#270): «Not interested» everywhere with readable priority bins, «Saved to Zotero» / «will be written when the Mac is connected», no journal/DOI prefix in abstract previews, no mid-sentence red-flag fragments or licence quotes, no «Outdated review» flash, folded duplicate Pending rows with local times.
- Review output cut off (#268): a digest/verifier call that hits the output limit is re-run once with a doubled output budget (≤32768) and recorded in the lineage; failure banners say why in plain words (cut off, connection dropped, no answer in time) instead of «Inspect the attempt error».
- Ask this paper no longer abstains on papers whose PDF text hides soft hyphens or zero-width spaces (e.g. NEJM AI): quotes are matched ignoring them, and «verified quote» is shown again (#271).
- Today, offline and conflict wording (#265): plain «still being added» banner; a paper with a review no longer flashes «needs a review» after Add/verdict; offline paper pages say «You are offline — AI review needs the Mac»; conflict banners show the paper title (feed-key titles now resolved) or «this paper»; Keep mine refreshes the open note.
- CLI errors and progress: `research-feed view` on a missing report, offline `prefetch-models` and a missing model cache give one-line errors or an empty inventory, not tracebacks; `doctor` says «Local model not tested» instead of «failed»; `calibrate` prints per-paper progress.
- Weekly/RSS wording (#264): «Dataset provenance» not the raw key, «1 card», capitalised Not-a-fit chips, readable «Last checked» and Markdown range; RSS refresh no longer rewrites identical items, so re-fetched feeds count «unchanged».
- Library, Setup and AI approval read plainly: no glued abstract text, «Papers the model is unsure about» / «Not interested» labels, `/setup` shows the current state, and AI models has an explicit «Approve this connection» button.
- Paper page and Today read plainly: «Loading today's papers…», «← Back», no raw item keys in Ask errors, one wording for verdicts, plain checklist/legend text, repeated red flags merged, bottom bar no longer covers text.
- Search and Find similar read in plain words and fit a 390 px screen: no sideways scroll, no raw HTML in abstracts, readable source and status labels, the saved query shown.
- Reviews you ask for no longer wait behind background work (#251): Generate/Regenerate and Add start before Today preparation, prewarm and fleet reviews (`queue_ahead` in review status); Search reviews 4 papers at a time, not 14 in a row.
- Search and Pending clarity (#299): «What was searched» rows read «Must mention (title/abstract)», «Filters» (AI-suggested vs confirmed by you) and «Needs your confirmation: …»; Pending folds re-queued versions of one note (same title) behind «N older versions».

### Added

- CLI/MCP (#249): busy-port `serve` and `feeds select-daily` exit promptly; bad `--inspect`/recipe/snapshot → one line, exit 2; `restore-model` says to restart; MCP `search_papers` ~10 calls instead of ~770; synthetic experiment examples (`python -m tools.method_validation.examples DIR`).
- RSS, Settings and offline wording (#248): plain feed errors, "Added N new feeds" and "Nothing new" refresh summaries, host-only feed rows, paper-named sync conflicts, one approval hint, iPhone home-screen icon.
- Background processing (#219): Settings task for papers reviewed at once (8), calls per paper (2), feed catch-up days (7) and pages per feed (10); saved to goals.yaml only when changed from the default.
- Checklist engine (#147): Settings choice Off (default) / Local / Cloud. When on, an external checklist model answers up to 5 checklist items (code/data, uncertainty, ablation, baselines, outside validation) with a verified evidence passage; unsure or failing cases fall back to the language model. Local = your own server address, not validated; Cloud sends paper text externally.
- Your decisions (#47, #218): Ops → System analyses your accumulated verdicts (what changed, agreement with saved predictions, per-class ranges, data gaps) with “Run now”; also weekly with the feed daemon (switch in that card). Read-only: labels and models never change.
- Review depth (#143): Settings choice Thorough (default) or Fast. Fast turns off the digest model's reasoning and runs the rubric once: writing step 50–75% quicker, but wording changed on 3 of 4 test papers and 1 grade changed. Other-depth reviews show as outdated.
- Paper page: **Evidence** fold (#40) with the verbatim quotes behind each verified claim, grouped by field (read-only `GET /api/library/review-audit/{key}`); summary card with verdict, why, read first, code row; sticky verdict bar; collapsible sections.
- Today (#37, #124, #151, #159, #163, #165, #172, #183): one column, one «New shortlist», per-card must/should/could/skip with undo and reason chips; decision shown after a verdict; «Not reviewed yet» shelf; outdated reviews get a badge + Regenerate; «k of n checks».
- Cards (#98, #108, #114, #127): AI card with TL;DR, key result, novelty/code/h-index strip and one plain why-line naming the goal; ≤4 labelled bullets, English UI; generated ahead for Today's top-15 under per-paper and daily caps and a circuit breaker.
- Zotero note (#152): carries the whole review: digest fields, rubric with quotes, k of n checks, red flags, overstatements, type, goal board, code link, section lines, verification. Writes still follow approval and a backup.
- Paper types (#169, #180): `experience_report`; stricter position-paper checklist; «type uncertain» graded by the generic checklist.
- Telegram (#99, #112, #119–#126, #128, #157, #167, #170, #174, #184): one «Check channels» job with progress and remembered consent; whole discussions read; comments load on tap; one line per key comment; «Disagreement» only when detected; trends; archive by date; feedback chips + note.
- Local model Test checks memory first (#117): reads RAM/swap and model size, sends one ≤16-token prompt only if safe, otherwise returns `memory_insufficient`/`swap_nearly_full`/`model_size_unknown` with numbers.
- Today logs what it shows (local `data/today-exposure/`; off: `ZS_TODAY_EXPOSURE_LOG=0`) and adds up to two marked “Random check” papers per day (`ZS_TODAY_EXPLORATION_SLOTS`). Ranking: goal-text cosine (`ZS_TODAY_RANKER=composite_v1` restores the old order).
- Search and discovery: targeted Search over arXiv/Europe PMC/OpenAlex/Crossref/Semantic Scholar, find-new-relevant for saved papers, Related links, Weekly paper-gap review, optional question-evidence retrieval; opt-in offline comparisons.
- Offline PWA review on iPhone (Tailscale HTTPS or private socket): working set, queued saves/notes/questions, conflict choices, ordered sync; Ask grounded questions about a paper with quotes.
- Deep review features: map-reduce digests, parameter extraction, code-repo links, figure attachment, review notes mirrored to Zotero, one-tap Confirm/Override on proposed verdicts; review fleet with university browser access for gated PDFs.
- Setup and deployment: first-run setup (local, hosted or no LLM) validated by Doctor; deployment profiles with opt-in “Calibrate to my setup”; `ZS_AUTOMATIC_GATE_UPDATES=0` pins a retained model; local weights run from another volume with bounded batch/KV size.
- Sources: PubMed (with PMC full text), HackerNoon and flagship-journal feeds; Pending Changes keyboard navigation; Telegram as a source with one-time terminal authorization.

### Changed

- Ops wording (#246): a note made for another Zotero library says so (header pending count refreshes after Apply/Reject); retrain text matches the restorable model history; plain model-card, readiness and Gate-rejected labels.
- Weekly digests in plain words (#247): date ranges, “Worth a skim”, sentences instead of snake_case flags and raw gate scores, project use said once, link-only Telegram posts titled by their first text line, “2 comments” labels.
- UI noise leftovers (#237): plain Ops pending-changes copy, one chevron for every disclosure (no native ▶), shared label style on paper-page rail headings, 8 px shorter iPhone nav.
- Field repair (#191) tries a fast attempt without reasoning first and escalates once with reasoning only if the field is still rejected (repair call ~10 s instead of 17–89 s); a field emptied by repair is listed as not established; lineage records rejected fields.
- Calmer pages (#227): the paper verdict is on the first iPhone screen (long author lists fold to `+N more`, facts under the verdict, one-line outdated note); Today shows «Quality A», hides feed codes; Library hides raw HTML and folds explainers; no weight ≥600 anywhere.
- Calmer Settings (#230): each provider failure and address is shown once (rows say «Not reachable»; reason on AI models, addresses under «Providers and status checks»); Needs-attention lists only open issues; short «Change» actions; repeated notes folded; Forest checkboxes.
- Paper card facts (#231): the icon row shows only the facts that were found (one row on iPhone); with none, one quiet line instead of 11 empty circles. “more” still lists every checked fact.
- Dead-code, duplication and overlap checks now run through deadcode-audit, always on its latest main (`make lint`/`make install` refresh it; offline falls back). Diff gates run at pre-push and in `make lint`; the local scripts and allowlists are gone (#223).
- Whole paper body in review (#136): every judging stage reads the whole body minus references, boilerplate and page furniture (256k window; longer papers are split and merged). Prompts grow up to ~2.5× on long papers; existing reviews show as outdated.
- Deep review speed (#135, #137, #141, #180): quality layers run beside the digest, light calls no longer reason, section one-liners build on open: −2…−5 calls per review; grade, band and goals unchanged on 4 test papers. The claims check and the overstatement check stay two separate calls (#134).
- Review fleet (#193): 8 papers at once × 2 calls each (was 4×4): 8 test papers in 324 s instead of 668 s, no rate-limit errors. `quality_review.fleet_papers`/`calls_per_paper` = 0 restores the old split.
- Review repair (#130): a digest field the verifier cannot ground is repaired by one call, else shown as «Could not be grounded · Regenerate»; the review is kept. Only required fields (TL;DR, read-why, key findings) still fail it.
- Goal board and overstatements (#187, #194) only inform: shown as badge and flag, never change a verdict, read decision or Today eligibility. Proposal confidence drops only for an `uncertain` quality band.
- Owner verdicts store the app's proposal at decision time (#224, migration v18 `label_verdicts.proposal_json`); feedback analysis reports app-vs-owner agreement. Auto quality-gate verdicts are no longer training labels (#111).
- Feeds keep every entry (#161, #217): arXiv queries are paged back to a per-feed watermark (`feeds.backfill_days` 7, `max_pages_per_feed` 10); “Incomplete fetch” shown when cut off. Cards show the last author's h-index.
- Settings: saved-first AI connections (Connect, catalog or manual choice, impact preview, explicit Use); one table of which model runs each step with median time and status; scoped saves; article selection/RSS lives in Today and Telegram.
- Today separates relevance from AI evidence; feed papers need a current full-text review before Add or a positive feed label; the daemon no longer auto-adds or rejects picks. Today and Library read as terse decision cards with readable counts.
- Review quality: grades come from a validated deterministic checklist, keep idea/evidence/writing separate and cap advice for weak evidence; verification checks critical criteria against source context and runs against the original paper only.
- Writes are safe: Zotero note/tag writes and raw RSS read-state updates back up first; pending changes reject as one transaction; remote or local model operations need explicit consent and a resource envelope.
- Search constraints need your confirmation; library membership stays unknown when it cannot be checked. Weekly (#128) is English with one status line and terse Telegram gists in the chat's language.
- Make: bare `make` shows help; `make install` and `make run` (rebuilds a stale UI); `make iphone` maps Tailscale HTTPS. Compact app chrome, gzip for JSON/static over 1 KB, `/` opens Today.

### Fixed

- Find similar papers no longer marks its AI check as stale after unrelated background work.
- Zotero (#254): applying an `upsert note` replaces the paper's earlier app-written digest/verdict/My-notes note in place, including legacy ones whose marker was HTML-escaped, and trashes extra owned duplicates, so one note per paper.
- Connection approval (#244): an approved server stays approved across unrelated Settings saves, other providers' key saves and Telegram edits; it expires only when that provider's address/auth or its own key changes (one re-approval for older approvals).
- Reviews (#242): web articles with non-breaking spaces or soft hyphens now render to PDF; heavy pages no longer fail on the browser proxy's connection cap; concurrent reviews wait for the browser instead of skipping it; Today preparation starts ~30 s after launch.
- Find similar (#216, #225): works for feed-only papers too, sits right under the review, shows progress, drops the paper itself from results; library membership is cached until the library changes (/discover p50 1.1 s → 0.13 s).
- Outdated-but-valid reviews (#116) keep the card recommended, accept verdict and Add for the same PDF, and show an «Outdated review» badge + refresh instead of a blank panel; review identity tracks only inputs that shape content.
- Model Test (#101) keeps the true local memory-safety reason; connection approval is told apart from network failure with exact-connection recovery; setup cancellation or credential changes invalidate stale Ready.
- Telegram live collection (#99): bare auto-linked domains no longer void a fetched page; posts carry `replies_count`; the gate scorer loads SPECTER2.
- Owner relabels of an auto quality-gate hide keep no machine comment on your `user` row; Trash/Add toasts report pending sync and label errors; double Add no longer duplicates; Search/Related results are deduped.
- PDF and full text: browser-like User-Agent for open-access PDFs, bioRxiv/medRxiv `.full.pdf` fallback, Cloudflare and paywalled PDFs via the browser rung, text-only snapshots kept apart from publisher PDFs.
- Figures: stale renders show «Rebuilding figures…» or «Rebuild»; Figures and Tables are listed separately. Number normalisation and line-break artifacts no longer falsely reject sound claims.
- Deep review no longer polls forever on a stalled response, retries once on malformed replies and reports the real failure; malformed saved rows give per-paper errors; missing values stay unknown, not zero.
- Mobile: banners wrap, tabs wrap on narrow screens, 320/390 px reflow, offline Generate disabled; page layout polish (#124–#126 follow-up). RSS refresh rotates feeds; Retrain no longer double-runs; prestige no longer claims `h=0`.

### Removed

- Telegram: the duplicate CLI «saved news» pipeline (`research-feed run --source telegram`, `run_saved`, `TelegramNewsVerdict`, v1 reports), and the dead `conversations` path; the Re-label Audit page; Today's per-card Quality expander; legacy `llm.draft_model`/`refine_model`.
- `make api`, `make ui` and `make ui-build` (use `make run` or `make dev`); `serve --no-kill`: it reports the bind error instead of stopping another process; `docs/benchmarking.md` is local-only.
### Fixed

- Readable labels: Weekly saved digests show date ranges, the Telegram sign-in command includes `zotero-summarizer`, `research-feed run --cached-only` names papers by title and exits 0 for uncached ones, Pending changes use plain action names and drop the internal band.

