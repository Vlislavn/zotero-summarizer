# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Current behavior lives in [docs/features.md](docs/features.md); history only.

## [Unreleased]

### Added

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
